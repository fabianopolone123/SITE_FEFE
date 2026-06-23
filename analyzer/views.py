import json
import os
import re
import subprocess
import tempfile
import threading
import uuid as _uuid_mod
from datetime import datetime
from html import escape
from pathlib import Path

from django.db import transaction
from django.shortcuts import get_object_or_404, render
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone

from .parser import parse_pdf
from .calculator import calculate
from .pdf_generator import generate_pdf
from .models import ProcessedReport, StudentGrade, StudentSnapshot, SubjectSnapshot
from .parser import extract_pdf_text, parse_from_text

BIMESTER_CHOICES = [
    ('auto', 'Detectar automaticamente'),
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


def _json_safe(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def _level_parts(level):
    if not level:
        return '', ''
    return level.get('key', ''), level.get('label', '')


@transaction.atomic
def _save_processed_report(data, metrics, teacher_name='', source_filename=''):
    lookup = {
        'source_filename': source_filename,
        'school': data['school'],
        'class_name': data['class_name'],
        'year': data['year'],
        'bimester': data['bimester'],
    }
    existing = list(ProcessedReport.objects.filter(**lookup).order_by('-processed_at', '-id'))
    report = existing[0] if existing else ProcessedReport(**lookup)

    if len(existing) > 1:
        ProcessedReport.objects.filter(id__in=[item.id for item in existing[1:]]).delete()

    report.teacher_name = teacher_name
    report.bimester_label = data['bimester_label']
    report.total_students = metrics['total_students']
    report.class_average = metrics['class_average']
    report.freq_media = metrics.get('freq_media')
    report.risk_count = metrics['risk_count']
    report.risk_pct = metrics['risk_pct']
    report.learning_index = metrics['learning_index']
    report.raw_data = _json_safe(data)
    report.metrics_snapshot = _json_safe(metrics)
    if report.pk:
        report.processed_at = timezone.now()
    report.save()

    report.subjects.all().delete()
    report.students.all().delete()

    SubjectSnapshot.objects.bulk_create([
        SubjectSnapshot(
            report=report,
            code=subject['code'],
            name=subject['name'],
            average=subject['average'],
            level_key=_level_parts(subject.get('level'))[0],
            level_label=_level_parts(subject.get('level'))[1],
        )
        for subject in metrics['subject_averages']
    ])

    student_objs = []
    source_students = []
    for student in metrics['students']:
        level_key, level_label = _level_parts(student.get('level'))
        obj = StudentSnapshot(
            report=report,
            num=student.get('num'),
            name=student['name'],
            ra=student.get('ra', ''),
            active=student.get('active', True),
            total_faltas=student.get('total_faltas'),
            average=student.get('average'),
            level_key=level_key,
            level_label=level_label,
        )
        student_objs.append(obj)
        source_students.append(student)

    StudentSnapshot.objects.bulk_create(student_objs)

    grade_objs = []
    subjects = data['subjects']
    subject_names = {s['code']: s['name'] for s in metrics['subject_averages']}
    for obj, student in zip(student_objs, source_students):
        for idx, code in enumerate(subjects):
            grades = student.get('grades') or []
            grade = grades[idx] if idx < len(grades) else None
            grade_objs.append(StudentGrade(
                student=obj,
                subject_code=code,
                subject_name=subject_names.get(code, code),
                grade=grade,
            ))

    StudentGrade.objects.bulk_create(grade_objs)
    return report


def _get_evolution(bimester: str, current_data: dict, current_metrics: dict | None = None) -> dict | None:
    """
    Compara o bimestre atual com o 1º bimestre salvo no banco.
    Usa somente a média geral da turma como parâmetro.
    """
    if bimester not in ('2', '3', '4'):
        return None

    base_report = (
        ProcessedReport.objects
        .prefetch_related('students')
        .filter(
            school=current_data['school'],
            class_name=current_data['class_name'],
            year=current_data['year'],
            bimester='1',
        )
        .order_by('-processed_at', '-id')
        .first()
    )
    if not base_report:
        return None

    curr_mean = None
    if current_metrics and current_metrics.get('class_average') is not None:
        curr_mean = float(current_metrics['class_average'])
    prev_mean = float(base_report.class_average) if base_report.class_average is not None else None

    if curr_mean is None or prev_mean is None:
        return None

    diff_mean = round(curr_mean - prev_mean, 1)
    variacao_str = ('+' if diff_mean > 0 else '') + _fmt(diff_mean)

    if prev_mean != 0:
        pct_change = round((curr_mean - prev_mean) / prev_mean * 100, 1)
    else:
        pct_change = 0.0
    pct_str = ('+' if pct_change > 0 else '') + _fmt(pct_change) + '%'

    if diff_mean >= 0.5:
        dominant = 'cres'
    elif diff_mean <= -0.5:
        dominant = 'queda'
    else:
        dominant = 'estab'

    return {
        'cres':     pct_str if dominant == 'cres'  else '–',
        'estab':    pct_str if dominant == 'estab' else '–',
        'queda':    pct_str if dominant == 'queda' else '–',
        'variacao': variacao_str,
        'dominant': dominant,
    }


def _extract_pdf_text(pdf_file) -> str:
    """Lê e extrai o texto do PDF uma única vez."""
    try:
        pdf_file.seek(0)
    except (AttributeError, OSError):
        pass
    return extract_pdf_text(pdf_file)


def _parse_pdf_period(pdf_text: str, bimester: str) -> dict:
    return parse_from_text(pdf_text, bimester)


def _collect_periods_to_save(pdf_text: str, selected_bimester: str, active_data: dict) -> list:
    if selected_bimester not in ('auto', '', None):
        return [active_data]

    periods = {}
    for period in ('1', '2', '3', '4'):
        try:
            data = parse_from_text(pdf_text, period)
            periods[data['bimester']] = data
        except ValueError:
            continue

    if active_data['bimester'] not in periods:
        periods[active_data['bimester']] = active_data

    return [periods[key] for key in sorted(periods.keys())]


def _build_html_report(data, metrics, teacher_name='', evolution=None):
    """
    Lê o template HTML e injeta os dados calculados via JavaScript.
    Retorna o HTML completo como string.
    """
    html = HTML_TEMPLATE.read_text(encoding='utf-8')
    html = re.sub(
        r'Compara.{1,4}o com o bimestre anterior',
        'Comparação com o 1º bimestre',
        html,
        count=1,
    )

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
    risk_blocks = []
    for student in metrics['at_risk']:
        subject_rows = []
        for subject in student['critical_subjects']:
            level = subject.get('level') or {}
            color = level.get('color', '#e67e22')
            subject_rows.append(
                '<div class="obsRiskSubject">'
                f'<span>{escape(subject["name"])}</span>'
                f'<b style="color:{color}">{_fmt(subject["grade"])}</b>'
                '</div>'
            )
        risk_blocks.append(
            '<section class="obsRiskStudent">'
            f'<h4>{escape(student["name"])}</h4>'
            '<div class="obsRiskSubjects">'
            + ''.join(subject_rows) +
            '</div>'
            '</section>'
        )

    if risk_blocks:
        mid = (len(risk_blocks) + 1) // 2
        second_list = ''
        lists_class = 'obsRiskLists'
        if len(risk_blocks) > 4:
            first_blocks = ''.join(risk_blocks[:mid])
            second_blocks = ''.join(risk_blocks[mid:])
            lists_class += ' twoCols'
            second_list = '<div class="obsRiskList">' + second_blocks + '</div>'
        else:
            first_blocks = ''.join(risk_blocks)
        obs7_html = (
            '<div class="obsRisk">'
            f'<div class="obsRiskTop" style="border-color:{alert_color};background:{alert_bg};color:{alert_fg}">'
            f'<strong>{alert_status}</strong>'
            f'<span>{metrics["risk_count"]} aluno(s) com nota abaixo de 6,0</span>'
            f'<small>{desc}</small>'
            '</div>'
            f'<div class="{lists_class}">'
            '<div class="obsRiskList">'
            + first_blocks +
            '</div>'
            + second_list +
            '</div>'
            '</div>'
        )
    else:
        obs7_html = (
            '<div class="obsRisk obsRiskEmpty">'
            f'<div class="obsRiskTop" style="border-color:{alert_color};background:{alert_bg};color:{alert_fg}">'
            '<strong>NORMAL</strong>'
            '<span>Nenhum aluno com nota abaixo de 6,0</span>'
            '<small>Manter acompanhamento regular da turma.</small>'
            '</div></div>'
        )

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
        'obs7': obs7_html,
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

    index_url = reverse('index')
    injection += f"""
<script>
(function() {{
  var toolbar = document.querySelector('.toolbar');
  if (!toolbar) return;
  var btn = document.createElement('a');
  btn.href = '{index_url}';
  btn.textContent = '← Menu inicial';
  btn.style.cssText = 'background:#fff;color:#1a3a5c;border:1px solid #1a3a5c;border-radius:8px;padding:10px 14px;font-weight:800;cursor:pointer;box-shadow:0 3px 10px #0002;text-decoration:none;font-size:14px;display:inline-flex;align-items:center;';
  toolbar.insertBefore(btn, toolbar.firstChild);
}})();
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
            '<div class="rname">' + escape(r['name']) + '</div>'
            '<div class="rlabel">' + escape(r['label']) + '</div>'
            '</div>'
            '<button class="btn" onclick="openReport(' + str(i) + ')">Abrir ' + escape(r['label']) + '</button>'
            '</div>'
        )

    errors_html = ''
    if errors:
        errors_html = (
            '<div class="errors">'
            '<strong>Erros ao processar:</strong><ul>'
            + ''.join('<li>' + escape(e) + '</li>' for e in errors)
            + '</ul></div>'
        )

    reports_json = json.dumps(
        [{'name': r['name'], 'label': r['label'], 'html': r['html']} for r in results],
        ensure_ascii=False
    ).replace('</', '<\\/')

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
        '<p>' + str(len(results)) + ' relatório(s) processado(s) com sucesso. Escolha o bimestre para abrir.</p></div>'
        + errors_html
        + cards_html
        + '<script>var R=' + reports_json + ';'
        'function openReport(i){'
        'var b=new Blob([R[i].html],{type:"text/html;charset=utf-8"});'
        'window.open(URL.createObjectURL(b),"_blank");}'
        '</script></body></html>'
    )


def index(request):
    if request.method == 'GET':
        return render(request, 'analyzer/index.html', {
            'bimester_choices': BIMESTER_CHOICES,
        })

    pdf_files  = request.FILES.getlist('pdf_file')
    bimester   = request.POST.get('bimester', 'auto')
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
            pdf_text = _extract_pdf_text(pdf_file)
            data      = _parse_pdf_period(pdf_text, bimester)
            period_data_list = _collect_periods_to_save(pdf_text, bimester, data)
            metrics_by_period = {}
            for period_data in period_data_list:
                metrics_by_period[period_data['bimester']] = calculate(period_data)
            metrics = metrics_by_period[data['bimester']]
            saved_reports = {}
            for period_data in period_data_list:
                period_metrics = metrics_by_period[period_data['bimester']]
                saved_reports[period_data['bimester']] = _save_processed_report(
                    period_data,
                    period_metrics,
                    teacher_name=teacher,
                    source_filename=pdf_file.name,
                )
            display_periods = period_data_list if bimester in ('auto', '', None) else [data]
            if output_fmt == 'html':
                if not HTML_TEMPLATE.exists():
                    raise ValueError('Template HTML não encontrado na pasta do projeto.')
                for period_data in display_periods:
                    period_metrics = metrics_by_period[period_data['bimester']]
                    evolution = _get_evolution(period_data['bimester'], period_data, period_metrics)
                    html_content = _build_html_report(period_data, period_metrics, teacher_name=teacher, evolution=evolution)
                    saved_report = saved_reports.get(period_data['bimester'])
                    results.append({
                        'name':  _clean_turma(period_data['class_name']),
                        'label': period_data['bimester_label'],
                        'html':  html_content,
                        'data':  period_data,
                        'metrics': period_metrics,
                        'report_id': saved_report.id if saved_report else None,
                    })
            else:
                for period_data in display_periods:
                    period_metrics = metrics_by_period[period_data['bimester']]
                    saved_report = saved_reports.get(period_data['bimester'])
                    pdf_bytes = generate_pdf(period_data, period_metrics)
                    results.append({
                        'name':  _clean_turma(period_data['class_name']),
                        'label': period_data['bimester_label'],
                        'pdf':   pdf_bytes,
                        'data':  period_data,
                        'report_id': saved_report.id if saved_report else None,
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


def history(request):
    reports = ProcessedReport.objects.all()[:200]
    return render(request, 'analyzer/history.html', {
        'reports': reports,
    })


def generate_from_history(request, report_id):
    report = get_object_or_404(ProcessedReport, id=report_id)
    html_content = _build_html_report(
        report.raw_data,
        report.metrics_snapshot,
        teacher_name=report.teacher_name,
        evolution=None,
    )
    return HttpResponse(html_content, content_type='text/html; charset=utf-8')


def history_details(request, report_id):
    report = get_object_or_404(
        ProcessedReport.objects.prefetch_related('subjects', 'students__grades'),
        id=report_id,
    )
    students = []
    for student in report.students.all():
        students.append({
            'name': student.name,
            'ra': student.ra,
            'average': _fmt(student.average),
            'level': student.level_label or '-',
            'total_faltas': student.total_faltas if student.total_faltas is not None else '-',
            'grades': [
                {
                    'subject_code': grade.subject_code,
                    'subject_name': grade.subject_name,
                    'grade': _fmt(grade.grade),
                }
                for grade in student.grades.all()
            ],
        })

    payload = {
        'id': report.id,
        'source_filename': report.source_filename,
        'teacher_name': report.teacher_name or '-',
        'school': report.school,
        'class_name': report.class_name,
        'year': report.year,
        'bimester_label': report.bimester_label,
        'processed_at': report.processed_at.strftime('%d/%m/%Y %H:%M'),
        'metrics': {
            'total_students': report.total_students,
            'class_average': _fmt(report.class_average),
            'freq_media': _fmt(report.freq_media),
            'risk_count': report.risk_count,
            'risk_pct': _fmt(report.risk_pct),
            'learning_index': _fmt(report.learning_index),
        },
        'subjects': [
            {
                'code': subject.code,
                'name': subject.name,
                'average': _fmt(subject.average),
                'level': subject.level_label or '-',
            }
            for subject in report.subjects.all()
        ],
        'students': students,
    }
    return JsonResponse(payload, json_dumps_params={'ensure_ascii': False})


# Armazena HTMLs temporários para o Chrome headless buscar via HTTP
_pending_reports: dict = {}
_pending_lock = threading.Lock()


def _find_chrome():
    from django.conf import settings as dj_settings
    env_path = getattr(dj_settings, 'CHROME_PATH', '')
    if env_path and os.path.exists(env_path):
        return env_path

    candidates = [
        # Linux
        '/usr/bin/google-chrome-stable',
        '/usr/bin/google-chrome',
        '/usr/bin/chromium-browser',
        '/usr/bin/chromium',
        '/snap/bin/chromium',
        '/usr/local/bin/chromium',
        # Windows
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
