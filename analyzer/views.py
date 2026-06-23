import json
import os
import re
import subprocess
import tempfile
import threading
import uuid as _uuid_mod
from datetime import datetime
from pathlib import Path

from django.shortcuts import render
from django.http import HttpResponse

from .parser import parse_pdf
from .calculator import calculate
from .pdf_generator import generate_pdf

BIMESTER_CHOICES = [
    ('1', '1º Bimestre'),
    ('2', '2º Bimestre'),
    ('3', '3º Bimestre'),
    ('4', '4º Bimestre'),
    ('anual', 'Ano Letivo'),
]

HTML_TEMPLATE = Path(__file__).parent.parent / 'mapa-puro-html-corrigido (1).html'


def _fmt(val):
    if val is None:
        return '-'
    return str(val).replace('.', ',')


def _clean_turma(name: str) -> str:
    return re.sub(r'\s*\([A-Z0-9]+\)\s*$', '', name).strip()


def _get_subj(code, subject_averages):
    for s in subject_averages:
        if s['code'] == code:
            return _fmt(s['average'])
    return '-'


def _get_evolution(pdf_file, bimester: str, current_data: dict) -> dict | None:
    """
    Lê o bimestre anterior do mesmo PDF e calcula evolução por aluno.
    Retorna dict com cres/estab/queda/variacao/dominant, ou None se indisponível.
    """
    if bimester not in ('2', '3', '4'):
        return None

    prev_bim = str(int(bimester) - 1)
    try:
        pdf_file.seek(0)
        prev_data = parse_pdf(pdf_file, prev_bim)
    except Exception:
        return None

    prev_avg_by_ra = {}
    for s in prev_data['students']:
        valid = [g for g in s['grades'] if g is not None]
        if valid:
            prev_avg_by_ra[s['ra']] = round(sum(valid) / len(valid), 1)

    cres = estab = queda = total = 0
    curr_avgs = []
    prev_avgs_matched = []

    for s in current_data['students']:
        valid = [g for g in s['grades'] if g is not None]
        if not valid:
            continue
        curr_avg = round(sum(valid) / len(valid), 1)
        curr_avgs.append(curr_avg)
        prev_avg = prev_avg_by_ra.get(s['ra'])
        if prev_avg is None:
            continue
        prev_avgs_matched.append(prev_avg)
        diff = round(curr_avg - prev_avg, 1)
        total += 1
        if diff >= 0.5:
            cres += 1
        elif diff <= -0.5:
            queda += 1
        else:
            estab += 1

    if total == 0:
        return None

    cres_pct  = round(cres  / total * 100, 1)
    estab_pct = round(estab / total * 100, 1)
    queda_pct = round(queda / total * 100, 1)

    curr_mean = round(sum(curr_avgs) / len(curr_avgs), 1) if curr_avgs else None
    prev_mean = round(sum(prev_avgs_matched) / len(prev_avgs_matched), 1) if prev_avgs_matched else None

    if curr_mean is not None and prev_mean is not None:
        diff_mean = round(curr_mean - prev_mean, 1)
        variacao_str = ('+' if diff_mean > 0 else '') + _fmt(diff_mean)
    else:
        variacao_str = '-'

    dominant = (
        'cres'  if cres  >= estab and cres  >= queda else
        'queda' if queda >= estab else
        'estab'
    )

    return {
        'cres':     _fmt(cres_pct),
        'estab':    _fmt(estab_pct),
        'queda':    _fmt(queda_pct),
        'variacao': variacao_str,
        'dominant': dominant,
    }


def _build_html_report(data, metrics, teacher_name='', evolution=None):
    """
    Lê o template HTML e injeta os dados calculados via JavaScript.
    Retorna o HTML completo como string.
    """
    html = HTML_TEMPLATE.read_text(encoding='utf-8')

    today = datetime.now()
    lv = metrics['level_distribution']   # [avancado, adequado, basico, critico]
    sa = metrics['subject_averages']
    risk_pct = metrics['risk_pct']
    class_avg = metrics['class_average'] or 0

    # ── Status de alerta pedagógico ───────────────────────────────────────
    if risk_pct > 30 or class_avg < 6.0:
        alert_status   = 'ALERTA'
        alert_color    = '#fc1230'
        alert_bg       = '#fdecea'
        alert_fg       = '#922b21'
    elif risk_pct > 10:
        alert_status   = 'ATENÇÃO'
        alert_color    = '#f4a51c'
        alert_bg       = '#fff8e1'
        alert_fg       = '#7d5a00'
    else:
        alert_status   = 'NORMAL'
        alert_color    = '#208a35'
        alert_bg       = '#eafaf1'
        alert_fg       = '#1d6a3b'

    # ── Conteúdo do campo de alerta (seção 7 — nota lateral) ─────────────
    box_style = (
        f'background:{alert_bg};border:2px solid {alert_color};'
        f'border-radius:5px;padding:14px 12px;height:100%;'
        f'box-sizing:border-box;display:flex;flex-direction:column;'
        f'align-items:center;justify-content:center;font-family:Arial,sans-serif'
    )

    if metrics['at_risk']:
        desc_lines = {
            'ATENÇÃO': 'Monitorar e planejar intervenções.',
            'ALERTA':  'Intervenção pedagógica prioritária.',
        }
        desc = desc_lines.get(alert_status, '')
        at_risk_html = (
            f'<div style="{box_style}">'
            f'<p style="margin:0 0 8px;font-weight:900;font-size:20px;'
            f'color:{alert_fg};text-transform:uppercase;letter-spacing:.06em;text-align:center">'
            f'{alert_status}</p>'
            f'<p style="margin:0 0 6px;font-size:13px;font-weight:700;color:{alert_fg};text-align:center">'
            f'{metrics["risk_count"]} aluno(s) com nota abaixo de 6,0</p>'
            f'<p style="margin:0;font-size:11px;color:{alert_fg};text-align:center;font-weight:600">'
            f'{desc}</p>'
            f'</div>'
        )
    else:
        at_risk_html = (
            f'<div style="{box_style}">'
            f'<p style="margin:0 0 8px;font-weight:900;font-size:20px;'
            f'color:{alert_fg};text-transform:uppercase;letter-spacing:.06em;text-align:center">'
            f'NORMAL</p>'
            f'<p style="margin:0;font-size:12px;font-weight:700;color:{alert_fg};text-align:center">'
            f'Nenhum aluno em nível Básico ou Crítico neste período.</p>'
            f'</div>'
        )

    # ── Meta sugerida (média atual + 0,5, máx 10) ────────────────────────
    if metrics['class_average'] is not None:
        meta_val = _fmt(min(round(metrics['class_average'] + 0.5, 1), 10.0))
    else:
        meta_val = '-'

    # ── Mapa de dados → data-key ──────────────────────────────────────────
    data_map = {
        # Cabeçalho página 1 e 2
        'p1-escola': data['school'],
        'p1-turma':  _clean_turma(data['class_name']),
        'p1-ano':    data['year'],
        'p1-prof':   teacher_name,
        'p1-d1':     today.strftime('%d'),
        'p1-d2':     today.strftime('%m'),
        'p1-d3':     today.strftime('%Y'),
        'p2-escola': data['school'],
        'p2-turma':  _clean_turma(data['class_name']),
        'p2-ano':    data['year'],
        'p2-prof':   teacher_name,
        'p2-d1':     today.strftime('%d'),
        'p2-d2':     today.strftime('%m'),
        'p2-d3':     today.strftime('%Y'),

        # Seção 1 — Resumo geral
        'total-alunos': str(metrics['total_students']),
        'media-geral':  _fmt(metrics['class_average']),
        'iat':          _fmt(round(metrics['learning_index'] / 10, 1)),
        'risco-qtd':    str(metrics['risk_count']),
        'risco-pct':    _fmt(metrics['risk_pct']),
        'meta':         meta_val,

        # Seção 2 — Distribuição por nível
        'avancado':   _fmt(lv[0]['pct']) + '%',
        'avancado-q': str(lv[0]['count']),
        'adequado':   _fmt(lv[1]['pct']) + '%',
        'adequado-q': str(lv[1]['count']),
        'basico':     _fmt(lv[2]['pct']) + '%',
        'basico-q':   str(lv[2]['count']),
        'critico':    _fmt(lv[3]['pct']) + '%',
        'critico-q':  str(lv[3]['count']),

        # Seção 3 — Desempenho por disciplina
        'lp':    _get_subj('LP',  sa),
        'mat':   _get_subj('M',   sa),
        'cie':   _get_subj('C',   sa),
        'his':   _get_subj('H',   sa),
        'geo':   _get_subj('G',   sa),
        'ing':   _get_subj('ING', sa),
        'ef':    _get_subj('EF',  sa),
        'er':    _get_subj('ER',  sa),
        'artes': _get_subj('ART', sa),

        # Seção 4 — Evolução (calculada do bimestre anterior quando disponível)
        'cres':     (evolution or {}).get('cres',     '-'),
        'estab':    (evolution or {}).get('estab',    '-'),
        'queda':    (evolution or {}).get('queda',    '-'),
        'variacao': (evolution or {}).get('variacao', '-'),

        # Seção 5 — Engajamento
        'freq': _fmt(metrics['freq_media']) if metrics.get('freq_media') is not None else '-',
        'ativ': '-',
        'part': '-',

        # Seção 6 — Taxa de risco
        'taxa-risco': _fmt(metrics['risk_pct']) + '%',
        'qtd-risco':  str(metrics['risk_count']),

        # Seção 7 — Alerta pedagógico
        'alerta-note': at_risk_html,
    }

    # Percentuais para os donuts
    a_pct  = lv[0]['pct']
    aq_pct = lv[1]['pct']
    b_pct  = lv[2]['pct']
    c_pct  = lv[3]['pct']

    # Índice do bimestre para marcar o checkbox (1-4 ou 0 para Ano Letivo)
    bim_index = {'1': 0, '2': 1, '3': 2, '4': 3}.get(data['bimester'], -1)

    # Categoria dominante de evolução para highlight visual
    evo_dominant = (evolution or {}).get('dominant', '')

    data_json = json.dumps(data_map, ensure_ascii=False)

    injection = """
<script>
(function() {
  /* ── Preenche todos os campos data-key ───────────────────── */
  var D = """ + data_json + """;
  document.querySelectorAll('[data-key]').forEach(function(el) {
    var v = D[el.dataset.key];
    if (v !== undefined) el.innerHTML = v;
  });


  /* ── Escola e turma: alinhamento (estilo via CSS) ────────── */
  ['p1-escola','p2-escola','p1-turma','p2-turma'].forEach(function(k) {
    var el = document.querySelector('[data-key="' + k + '"]');
    if (el) {
      el.style.display       = 'inline-block';
      el.style.verticalAlign = 'bottom';
    }
  });

  /* ── Marca o bimestre em CADA grupo .bim separadamente ───── */
  var bimIdx = """ + str(bim_index) + """;
  if (bimIdx >= 0) {
    document.querySelectorAll('.bim').forEach(function(group) {
      var checks = group.querySelectorAll('.boxcheck');
      if (checks[bimIdx]) {
        checks[bimIdx].style.background = '#002a5c';
        checks[bimIdx].style.border     = '2px solid #002a5c';
      }
    });
  }

  /* ── Atualiza donut de distribuição por nível ────────────── */
  var a=""" + str(a_pct) + """, aq=""" + str(aq_pct) + """, b=""" + str(b_pct) + """, c=""" + str(c_pct) + """;
  var s2 = a + aq, s3 = a + aq + b;
  document.querySelectorAll('.donut').forEach(function(donut) {
    donut.style.background =
      'conic-gradient(var(--teal-dark) 0 ' + a + '%, var(--blue) ' + a + '% ' + s2 + '%, ' +
      'var(--orange) ' + s2 + '% ' + s3 + '%, var(--red) ' + s3 + '% 100%)';
  });
  /* ── Atualiza donut de risco ─────────────────────────────── */
  var rp = """ + str(risk_pct) + """;
  document.querySelectorAll('.riskDonut').forEach(function(rd) {
    rd.style.background =
      'conic-gradient(var(--red) 0 ' + rp + '%, #dce0e6 ' + rp + '% 100%)';
  });

  /* ── Destaca status de alerta (lista lateral + borda do campo) */
  var alertStatus = '""" + alert_status + """';
  var alertColor  = '""" + alert_color + """';
  var statusMap = { 'NORMAL': 0, 'ATENÇÃO': 1, 'ALERTA': 2 };
  var idx = statusMap[alertStatus];
  var statusItems = document.querySelectorAll('.statusItem');
  statusItems.forEach(function(el, i) {
    if (i === idx) {
      el.style.background = idx === 0 ? '#eafaf1' : idx === 1 ? '#fff8e1' : '#fdecea';
      el.style.borderRadius = '4px';
      el.style.fontWeight = '900';
      el.style.border = '1px solid ' + alertColor;
    }
  });

  /* Borda colorida no campo de alerta */
  var alertNote = document.querySelector('[data-key="alerta-note"]');
  if (alertNote) {
    alertNote.style.border = '2px solid ' + alertColor;
    alertNote.style.borderRadius = '7px';
    alertNote.style.overflow = 'hidden';
  }

  /* ── Destaca linha dominante de evolução ─────────────────── */
  var evoDom = '""" + evo_dominant + """';
  var evoMap = {cres: 0, estab: 1, queda: 2};
  var evoBg  = {cres: '#eafaf1', estab: '#ebf5fb', queda: '#fdecea'};
  if (evoDom && evoMap[evoDom] !== undefined) {
    var evoRows = document.querySelectorAll('.evoRow');
    if (evoRows[evoMap[evoDom]]) {
      evoRows[evoMap[evoDom]].style.background    = evoBg[evoDom];
      evoRows[evoMap[evoDom]].style.borderRadius  = '4px';
    }
  }

  /* ── Salva no localStorage para persistir ────────────────── */
  if (typeof save === 'function') save();
})();
</script>
"""

    html = html.replace('</body>', injection + '\n</body>')
    return html


def _build_results_page(results, errors):
    """Página de resultados para múltiplos PDFs processados."""
    cards_html = ''
    for i, r in enumerate(results):
        cards_html += (
            '<div class="rcard">'
            '<div class="rinfo">'
            '<div class="rname">' + r['name'] + '</div>'
            '<div class="rlabel">' + r['label'] + '</div>'
            '</div>'
            '<button class="btn" onclick="openReport(' + str(i) + ')">Abrir Relatório</button>'
            '</div>'
        )

    errors_html = ''
    if errors:
        errors_html = (
            '<div class="errors">'
            '<strong>Erros ao processar:</strong><ul>'
            + ''.join('<li>' + e + '</li>' for e in errors)
            + '</ul></div>'
        )

    open_all_btn = ''
    if len(results) > 1:
        open_all_btn = '<button class="btn btn-all" onclick="openAll()">Abrir Todos (' + str(len(results)) + ')</button>'

    reports_json = json.dumps(
        [{'name': r['name'], 'label': r['label'], 'html': r['html']} for r in results],
        ensure_ascii=False
    )

    return (
        '<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">'
        '<title>MAPA — Relatórios Gerados</title><style>'
        'body{font-family:Arial,sans-serif;background:#f0f4f8;padding:2rem;color:#092b5c}'
        '.header{background:linear-gradient(135deg,#002a5c,#2980b9);color:#fff;padding:2rem 2.5rem;'
        'border-radius:10px;margin-bottom:1.5rem;text-align:center}'
        '.header h1{margin:0 0 .4rem;font-size:2rem}'
        '.header p{margin:0;opacity:.85}'
        '.rcard{background:#fff;border-radius:8px;padding:1.2rem 1.5rem;margin:.7rem 0;'
        'box-shadow:0 2px 8px #0002;display:flex;justify-content:space-between;align-items:center;gap:1rem}'
        '.rname{font-weight:800;font-size:1rem;color:#002a5c}'
        '.rlabel{font-size:.85rem;color:#667;margin-top:.2rem}'
        '.btn{background:#002a5c;color:#fff;border:none;border-radius:6px;'
        'padding:.6rem 1.4rem;font-weight:700;cursor:pointer;font-size:.9rem;white-space:nowrap}'
        '.btn:hover{opacity:.85}'
        '.btn-all{background:#27ae60;display:block;margin:1.2rem auto 0;padding:.8rem 2rem;font-size:1rem}'
        '.errors{background:#fdecea;border-left:4px solid #e74c3c;border-radius:6px;'
        'padding:1rem;margin-bottom:1rem;font-size:.9rem}'
        '</style></head><body>'
        '<div class="header"><h1>MAPA — Relatórios Gerados</h1>'
        '<p>' + str(len(results)) + ' relatório(s) processado(s) com sucesso</p></div>'
        + errors_html
        + cards_html
        + open_all_btn
        + '<script>var R=' + reports_json + ';'
        'function openReport(i){'
        'var b=new Blob([R[i].html],{type:"text/html;charset=utf-8"});'
        'window.open(URL.createObjectURL(b),"_blank");}'
        'function openAll(){R.forEach(function(_,i){setTimeout(function(){openReport(i);},i*300);});}'
        '</script></body></html>'
    )


def index(request):
    if request.method == 'GET':
        return render(request, 'analyzer/index.html', {
            'bimester_choices': BIMESTER_CHOICES,
        })

    pdf_files  = request.FILES.getlist('pdf_file')
    bimester   = request.POST.get('bimester', '1')
    teacher    = request.POST.get('teacher', '').strip()
    output_fmt = request.POST.get('output_format', 'html')

    if not pdf_files:
        return render(request, 'analyzer/index.html', {
            'bimester_choices': BIMESTER_CHOICES,
            'error': 'Nenhum arquivo enviado. Selecione um ou mais PDFs.',
            'selected_bimester': bimester,
        })

    for f in pdf_files:
        if not f.name.lower().endswith('.pdf'):
            return render(request, 'analyzer/index.html', {
                'bimester_choices': BIMESTER_CHOICES,
                'error': f'Arquivo "{f.name}" não é um PDF.',
                'selected_bimester': bimester,
            })

    # ── Processa cada PDF ─────────────────────────────────────────────────
    results = []
    errors  = []

    for pdf_file in pdf_files:
        try:
            data      = parse_pdf(pdf_file, bimester)
            metrics   = calculate(data)
            evolution = _get_evolution(pdf_file, bimester, data)
            if output_fmt == 'html':
                if not HTML_TEMPLATE.exists():
                    raise ValueError('Template HTML não encontrado na pasta do projeto.')
                html_content = _build_html_report(data, metrics, teacher_name=teacher, evolution=evolution)
                results.append({
                    'name':  _clean_turma(data['class_name']),
                    'label': data['bimester_label'],
                    'html':  html_content,
                    'data':  data,
                    'metrics': metrics,
                })
            else:
                pdf_bytes = generate_pdf(data, metrics)
                results.append({
                    'name':  _clean_turma(data['class_name']),
                    'label': data['bimester_label'],
                    'pdf':   pdf_bytes,
                    'data':  data,
                })
        except ValueError as e:
            errors.append(f'{pdf_file.name}: {e}')
        except Exception as e:
            errors.append(f'{pdf_file.name}: erro inesperado — {e}')

    if not results:
        return render(request, 'analyzer/index.html', {
            'bimester_choices': BIMESTER_CHOICES,
            'error': ' | '.join(errors),
            'selected_bimester': bimester,
        })

    # ── Saída HTML ────────────────────────────────────────────────────────
    if output_fmt == 'html':
        if len(results) == 1 and not errors:
            return HttpResponse(results[0]['html'], content_type='text/html; charset=utf-8')
        return HttpResponse(_build_results_page(results, errors), content_type='text/html; charset=utf-8')

    # ── Saída PDF ─────────────────────────────────────────────────────────
    if len(results) == 1 and not errors:
        r = results[0]
        slug = r['data']['class_name'].replace(' ', '_').replace('/', '-')[:40]
        filename = f"MAPA_{slug}_{r['data']['bimester_label'].replace(' ', '_')}.pdf"
        response = HttpResponse(r['pdf'], content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response

    # Múltiplos PDFs → ZIP
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for r in results:
            slug = r['data']['class_name'].replace(' ', '_').replace('/', '-')[:30]
            fname = f"MAPA_{slug}_{r['data']['bimester_label'].replace(' ', '_')}.pdf"
            zf.writestr(fname, r['pdf'])
    buf.seek(0)
    resp = HttpResponse(buf.read(), content_type='application/zip')
    resp['Content-Disposition'] = 'attachment; filename="MAPA_relatorios.zip"'
    return resp


# Armazena HTMLs temporários para o Chrome headless buscar via HTTP
_pending_reports: dict = {}
_pending_lock = threading.Lock()


def _find_chrome():
    candidates = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _strip_extensions_scripts(html: str) -> str:
    """Remove scripts injetados por extensões (ex: Kaspersky) que quebram o headless."""
    return re.sub(
        r'<script[^>]+(?:kaspersky|kis\.v2\.scr|gc\.kis)[^>]*>.*?</script>',
        '', html, flags=re.DOTALL | re.IGNORECASE,
    )


def serve_temp_report(request, report_id):
    """Serve o HTML temporário para o Chrome headless."""
    with _pending_lock:
        html = _pending_reports.get(report_id)
    if html is None:
        return HttpResponse(status=404)
    return HttpResponse(html, content_type='text/html; charset=utf-8')


def export_pdf(request):
    """Recebe HTML via POST, serve via localhost e usa Chrome headless para gerar PDF."""
    if request.method != 'POST':
        return HttpResponse(status=405)

    html_raw = request.POST.get('html', '')
    raw_name = request.POST.get('filename', 'mapa-relatorio')
    filename = re.sub(r'[\\/*?:"<>|]', '', raw_name)[:80].strip() or 'mapa-relatorio'

    chrome = _find_chrome()
    if not chrome:
        return HttpResponse(
            'Chrome ou Edge não encontrado. Use o botão Imprimir e salve como PDF.',
            status=500, content_type='text/plain; charset=utf-8',
        )

    # Limpa scripts de extensão e salva temporariamente em memória
    html_clean = _strip_extensions_scripts(html_raw)
    report_id = str(_uuid_mod.uuid4())
    with _pending_lock:
        _pending_reports[report_id] = html_clean

    # URL acessível pelo Chrome headless via Django rodando localmente
    host = request.META.get('HTTP_HOST', '127.0.0.1:8000')
    page_url = f'http://{host}/temp-report/{report_id}/'

    pdf_path = os.path.join(tempfile.gettempdir(), f'mapa_{report_id}.pdf')

    try:
        base_flags = [
            chrome,
            '--disable-gpu',
            '--no-sandbox',
            '--disable-extensions',
            '--disable-dev-shm-usage',
            '--run-all-compositor-stages-before-draw',
            f'--print-to-pdf={pdf_path}',
            '--no-pdf-header-footer',
            '--print-to-pdf-no-header',
        ]
        result = None
        for headless_flag in ('--headless=new', '--headless'):
            cmd = [base_flags[0], headless_flag] + base_flags[1:] + [page_url]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode == 0 and os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 500:
                break
            try:
                os.unlink(pdf_path)
            except OSError:
                pass

        if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 500:
            stderr = (result.stderr if result else b'').decode('utf-8', errors='replace')
            return HttpResponse(
                f'Falha ao gerar PDF.\n{stderr[:400]}',
                status=500, content_type='text/plain; charset=utf-8',
            )

        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}.pdf"'
        return response

    except subprocess.TimeoutExpired:
        return HttpResponse('Timeout ao gerar PDF (>60s).', status=500, content_type='text/plain; charset=utf-8')
    except Exception as exc:
        return HttpResponse(f'Erro: {exc}', status=500, content_type='text/plain; charset=utf-8')
    finally:
        with _pending_lock:
            _pending_reports.pop(report_id, None)
        try:
            os.unlink(pdf_path)
        except OSError:
            pass
