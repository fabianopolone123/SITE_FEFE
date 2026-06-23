"""
Gera o relatório MAPA em PDF usando ReportLab.
"""

import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, white, black, Color
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether,
)
from reportlab.platypus.flowables import HRFlowable

# ── Paleta de cores ───────────────────────────────────────────────────────────
C_HEADER     = HexColor('#1a3a5c')   # azul escuro
C_SUBHEADER  = HexColor('#2980b9')   # azul médio
C_ACCENT     = HexColor('#3498db')   # azul claro
C_LIGHT_BG   = HexColor('#f0f4f8')   # fundo suave
C_BORDER     = HexColor('#bdc3c7')   # bordas
C_TEXT       = HexColor('#2c3e50')   # texto principal
C_MUTED      = HexColor('#7f8c8d')   # texto secundário
C_WHITE      = white

C_AVANCADO   = HexColor('#27ae60')
C_ADEQUADO   = HexColor('#f39c12')
C_BASICO     = HexColor('#e67e22')
C_CRITICO    = HexColor('#e74c3c')

LEVEL_COLORS = {
    'avancado': C_AVANCADO,
    'adequado': C_ADEQUADO,
    'basico':   C_BASICO,
    'critico':  C_CRITICO,
}

PAGE_W, PAGE_H = A4
MARGIN = 1.8 * cm


def _styles():
    base = getSampleStyleSheet()

    def _new(name, parent='Normal', **kw):
        s = ParagraphStyle(name, parent=base[parent], **kw)
        return s

    return {
        'title': _new('title',
            fontSize=20, textColor=C_WHITE, fontName='Helvetica-Bold',
            alignment=TA_CENTER, spaceAfter=2),
        'subtitle': _new('subtitle',
            fontSize=11, textColor=HexColor('#bde0ff'), fontName='Helvetica',
            alignment=TA_CENTER, spaceAfter=0),
        'section': _new('section',
            fontSize=10, textColor=C_WHITE, fontName='Helvetica-Bold',
            alignment=TA_LEFT, spaceAfter=0),
        'label': _new('label',
            fontSize=8, textColor=C_MUTED, fontName='Helvetica',
            alignment=TA_CENTER, spaceBefore=0, spaceAfter=0),
        'metric_val': _new('metric_val',
            fontSize=22, textColor=C_HEADER, fontName='Helvetica-Bold',
            alignment=TA_CENTER, spaceAfter=0),
        'metric_lbl': _new('metric_lbl',
            fontSize=8, textColor=C_MUTED, fontName='Helvetica',
            alignment=TA_CENTER, spaceBefore=0),
        'body': _new('body',
            fontSize=9, textColor=C_TEXT, fontName='Helvetica',
            alignment=TA_LEFT, spaceAfter=2),
        'risk_name': _new('risk_name',
            fontSize=9, textColor=C_TEXT, fontName='Helvetica-Bold',
            alignment=TA_LEFT),
        'risk_subj': _new('risk_subj',
            fontSize=8, textColor=C_MUTED, fontName='Helvetica',
            alignment=TA_LEFT),
        'footer': _new('footer',
            fontSize=7, textColor=C_MUTED, fontName='Helvetica',
            alignment=TA_CENTER),
    }


def _level_color(level_dict) -> Color:
    if not level_dict:
        return C_MUTED
    return LEVEL_COLORS.get(level_dict['key'], C_MUTED)


def _bar(value: float, max_val: float = 10.0, width: float = 100, height: float = 8,
         color: Color = C_ACCENT) -> str:
    """Retorna HTML de barra visual como string para Paragraph."""
    pct = min(value / max_val, 1.0)
    filled = int(pct * width)
    empty = width - filled
    c = color.hexval() if hasattr(color, 'hexval') else '#3498db'
    return (
        f'<font color="{c}">{"█" * (filled // 6)}</font>'
        f'<font color="#e0e0e0">{"█" * (empty // 6)}</font>'
    )


def generate_pdf(data: dict, metrics: dict) -> bytes:
    """
    Recebe o dict de dados parseados e o dict de métricas calculadas.
    Retorna os bytes do PDF gerado.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title='MAPA - Relatório de Aprendizagem',
    )

    S = _styles()
    story = []
    usable_w = PAGE_W - 2 * MARGIN

    today = datetime.now().strftime('%d/%m/%Y')

    # ══════════════════════════════════════════════════════════════════════
    # BLOCO 1 — Cabeçalho
    # ══════════════════════════════════════════════════════════════════════
    header_table = Table(
        [[
            Paragraph(data['school'], S['title']),
            Paragraph(
                'MAPA — Monitoramento de Aprendizagem Pedagógica Anual',
                S['subtitle'],
            ),
            Paragraph(
                f"{data['class_name']}  ·  {data['bimester_label']}  ·  {data['year']}",
                S['subtitle'],
            ),
        ]],
        colWidths=[usable_w],
    )
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_HEADER),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [C_HEADER]),
        ('TOPPADDING', (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
        ('LEFTPADDING', (0, 0), (-1, -1), 16),
        ('RIGHTPADDING', (0, 0), (-1, -1), 16),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))

    # Empilha escola + subtítulos numa célula única
    header_content = [
        Paragraph(data['school'], S['title']),
        Paragraph('MAPA — Monitoramento de Aprendizagem Pedagógica Anual', S['subtitle']),
        Paragraph(
            f"{data['class_name']}  ·  {data['bimester_label']}  ·  {data['year']}",
            S['subtitle'],
        ),
    ]
    header_block = Table([[header_content]], colWidths=[usable_w])
    header_block.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_HEADER),
        ('TOPPADDING', (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
        ('LEFTPADDING', (0, 0), (-1, -1), 16),
        ('RIGHTPADDING', (0, 0), (-1, -1), 16),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(header_block)
    story.append(Spacer(1, 0.35 * cm))

    # ══════════════════════════════════════════════════════════════════════
    # BLOCO 2 — Cartões de resumo
    # ══════════════════════════════════════════════════════════════════════
    def _card(value_str, label_str):
        return [
            Paragraph(value_str, S['metric_val']),
            Paragraph(label_str, S['metric_lbl']),
        ]

    li = metrics['learning_index']
    ca = metrics['class_average']
    ca_str = f"{ca:.1f}".replace('.', ',') if ca else '-'
    li_str = f"{li:.1f}%".replace('.', ',')

    cards_data = [[
        _card(str(metrics['total_students']), 'Alunos Ativos'),
        _card(ca_str, 'Média Geral da Turma'),
        _card(li_str, 'Índice de Aprendizagem'),
        _card(f"{metrics['risk_pct']:.1f}%".replace('.', ','),
              'Taxa de Alunos em Risco'),
    ]]

    col_w = usable_w / 4
    cards_table = Table(cards_data, colWidths=[col_w] * 4)
    cards_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_LIGHT_BG),
        ('BOX', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(cards_table)
    story.append(Spacer(1, 0.4 * cm))

    # ══════════════════════════════════════════════════════════════════════
    # BLOCO 3 — Médias por Disciplina
    # ══════════════════════════════════════════════════════════════════════
    def _section_header(text, width=None):
        # Remove emojis — fontes PDF padrão nao suportam Unicode emoji
        clean = text
        for emoji in ('📊', '🧠', '🚨', '📉', '🎯', '✅', '⚠'):
            clean = clean.replace(emoji, '').strip()
        w = width or usable_w
        t = Table([[Paragraph(clean, S['section'])]], colWidths=[w])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), C_SUBHEADER),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ]))
        return t

    story.append(_section_header('📊  MÉDIAS POR DISCIPLINA'))
    story.append(Spacer(1, 0.15 * cm))

    subj_header = ['Disciplina', 'Média', 'Nível', 'Desempenho']
    subj_col_w = [usable_w * 0.35, usable_w * 0.10, usable_w * 0.17, usable_w * 0.38]

    subj_rows = [subj_header]
    for s in metrics['subject_averages']:
        avg = s['average']
        avg_str = f"{avg:.1f}".replace('.', ',') if avg else '-'
        lvl = s['level']
        lvl_label = lvl['label'] if lvl else '-'
        lvl_color = _level_color(lvl)

        bar_pct = min(int((avg or 0) / 10 * 30), 30)
        bar_str = '█' * bar_pct + '░' * (30 - bar_pct)

        subj_rows.append([
            Paragraph(s['name'], S['body']),
            Paragraph(f'<b>{avg_str}</b>', ParagraphStyle(
                'avg', parent=S['body'], alignment=TA_CENTER,
                textColor=lvl_color, fontName='Helvetica-Bold')),
            Paragraph(f'<b>{lvl_label}</b>', ParagraphStyle(
                'lvl', parent=S['body'], alignment=TA_CENTER,
                textColor=lvl_color, fontName='Helvetica-Bold', fontSize=8)),
            Paragraph(bar_str, ParagraphStyle(
                'bar', parent=S['body'], textColor=lvl_color, fontSize=7)),
        ])

    subj_table = Table(subj_rows, colWidths=subj_col_w, repeatRows=1)
    subj_style = TableStyle([
        # Cabeçalho
        ('BACKGROUND', (0, 0), (-1, 0), C_HEADER),
        ('TEXTCOLOR', (0, 0), (-1, 0), C_WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        # Dados
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('ALIGN', (1, 1), (2, -1), 'CENTER'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, C_LIGHT_BG]),
        ('BOX', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('INNERGRID', (0, 0), (-1, -1), 0.3, C_BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ])
    subj_table.setStyle(subj_style)
    story.append(subj_table)
    story.append(Spacer(1, 0.4 * cm))

    # ══════════════════════════════════════════════════════════════════════
    # BLOCO 4 — Domínio das Habilidades por Nível
    # ══════════════════════════════════════════════════════════════════════
    story.append(_section_header('🧠  DOMÍNIO DAS HABILIDADES POR NÍVEL'))
    story.append(Spacer(1, 0.15 * cm))

    n_total = metrics['total_students']
    level_rows = []
    for lv in metrics['level_distribution']:
        pct = lv['pct']
        bar_filled = int(pct / 100 * 40)
        bar_empty = 40 - bar_filled
        color = HexColor(lv['color'])
        bar_str = '█' * bar_filled + '░' * bar_empty

        level_rows.append([
            Paragraph(f"<b>{lv['label']}</b>", ParagraphStyle(
                'lk', parent=S['body'], textColor=color, fontName='Helvetica-Bold')),
            Paragraph(
                f"<b>{lv['count']}</b> aluno{'s' if lv['count'] != 1 else ''}",
                ParagraphStyle('lc', parent=S['body'], alignment=TA_CENTER)),
            Paragraph(f"<b>{pct:.1f}%</b>".replace('.', ','),
                ParagraphStyle('lp', parent=S['body'], alignment=TA_CENTER,
                               textColor=color, fontName='Helvetica-Bold')),
            Paragraph(bar_str, ParagraphStyle(
                'lb', parent=S['body'], textColor=color, fontSize=7)),
        ])

    lv_col_w = [usable_w * 0.18, usable_w * 0.18, usable_w * 0.14, usable_w * 0.50]
    level_table = Table(level_rows, colWidths=lv_col_w)
    level_table.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [white, C_LIGHT_BG]),
        ('BOX', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('INNERGRID', (0, 0), (-1, -1), 0.3, C_BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(level_table)
    story.append(Spacer(1, 0.4 * cm))

    # ══════════════════════════════════════════════════════════════════════
    # BLOCO 5 — Taxa de Alunos em Risco
    # ══════════════════════════════════════════════════════════════════════
    at_risk = metrics['at_risk']
    risk_count = metrics['risk_count']
    risk_pct = metrics['risk_pct']

    risk_header_text = (
        f'🚨  ALUNOS EM RISCO — {risk_count} aluno{"s" if risk_count != 1 else ""} '
        f'({risk_pct:.1f}% da turma)'.replace('.', ',')
    )
    story.append(_section_header(risk_header_text))
    story.append(Spacer(1, 0.15 * cm))

    if not at_risk:
        story.append(Paragraph(
            '✅  Nenhum aluno identificado em nível de risco (Básico ou Crítico) '
            'neste período.',
            S['body'],
        ))
    else:
        risk_header_row = ['Aluno (RA)', 'Média', 'Nível Geral', 'Disciplinas em Risco']
        risk_col_w = [usable_w * 0.34, usable_w * 0.10, usable_w * 0.16, usable_w * 0.40]
        risk_rows = [risk_header_row]

        for r in at_risk:
            avg_str = f"{r['average']:.1f}".replace('.', ',') if r['average'] else '-'
            lvl = r['level']
            lvl_color = _level_color(lvl)
            lvl_label = lvl['label'] if lvl else '-'

            # Monta lista de disciplinas críticas
            subj_parts = []
            for cs in r['critical_subjects']:
                cs_color = _level_color(cs['level'])
                cs_grade = f"{cs['grade']:.1f}".replace('.', ',')
                subj_parts.append(
                    f"<font color='#{cs_color.hexval()[2:]}'>"
                    f"<b>{cs['name']}: {cs_grade}</b> ({cs['level']['label']})"
                    f"</font>"
                )

            risk_rows.append([
                Paragraph(f"<b>{r['name']}</b><br/><font size='7' color='grey'>RA {r['ra']}</font>",
                          S['body']),
                Paragraph(f'<b>{avg_str}</b>', ParagraphStyle(
                    'ra', parent=S['body'], alignment=TA_CENTER,
                    textColor=lvl_color, fontName='Helvetica-Bold')),
                Paragraph(f'<b>{lvl_label}</b>', ParagraphStyle(
                    'rl', parent=S['body'], alignment=TA_CENTER,
                    textColor=lvl_color, fontName='Helvetica-Bold', fontSize=8)),
                Paragraph(' | '.join(subj_parts), S['body']),
            ])

        risk_table = Table(risk_rows, colWidths=risk_col_w, repeatRows=1)
        risk_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), C_HEADER),
            ('TEXTCOLOR', (0, 0), (-1, 0), C_WHITE),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, 0), 6),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 1), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('ALIGN', (1, 1), (2, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, HexColor('#fff3f3')]),
            ('BOX', (0, 0), (-1, -1), 0.5, C_BORDER),
            ('INNERGRID', (0, 0), (-1, -1), 0.3, C_BORDER),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(risk_table)

    story.append(Spacer(1, 0.4 * cm))

    # ══════════════════════════════════════════════════════════════════════
    # BLOCO 6 — Disciplinas Críticas
    # ══════════════════════════════════════════════════════════════════════
    story.append(_section_header('DISCIPLINAS CRITICAS (menor media da turma)'))
    story.append(Spacer(1, 0.15 * cm))

    worst = metrics['critical_subjects_ranked'][:3]
    if worst:
        dc_header_row = [['#', 'Disciplina', 'Media', 'Nivel']]
        dc_rows = []
        for rank, s in enumerate(worst, 1):
            avg_str = f"{s['average']:.1f}".replace('.', ',') if s['average'] else '-'
            lvl = s['level']
            lvl_color = _level_color(lvl)
            dc_rows.append([
                Paragraph(f'<b>{rank}.</b>', ParagraphStyle(
                    'rk', parent=S['body'], alignment=TA_CENTER)),
                Paragraph(s['name'], S['body']),
                Paragraph(f'<b>{avg_str}</b>', ParagraphStyle(
                    'da', parent=S['body'], alignment=TA_CENTER,
                    textColor=lvl_color, fontName='Helvetica-Bold')),
                Paragraph(lvl['label'] if lvl else '-', ParagraphStyle(
                    'dl', parent=S['body'], alignment=TA_CENTER,
                    textColor=lvl_color, fontSize=8)),
            ])

        dc_col = [usable_w * 0.07, usable_w * 0.47, usable_w * 0.23, usable_w * 0.23]
        dc_table = Table(dc_header_row + dc_rows, colWidths=dc_col)
        dc_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), C_HEADER),
            ('TEXTCOLOR', (0, 0), (-1, 0), C_WHITE),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),
            ('ALIGN', (2, 1), (3, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, C_LIGHT_BG]),
            ('BOX', (0, 0), (-1, -1), 0.5, C_BORDER),
            ('INNERGRID', (0, 0), (-1, -1), 0.3, C_BORDER),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(dc_table)
    else:
        story.append(Paragraph('Sem dados suficientes.', S['body']))

    story.append(Spacer(1, 0.4 * cm))

    # ══════════════════════════════════════════════════════════════════════
    # BLOCO 7 — Índice de Aprendizagem
    # ══════════════════════════════════════════════════════════════════════
    li_val = metrics['learning_index']
    satisfactory = (metrics['level_distribution'][0]['count'] +
                    metrics['level_distribution'][1]['count'])
    li_color = C_AVANCADO if li_val >= 80 else (C_ADEQUADO if li_val >= 60 else C_CRITICO)
    li_hex = li_color.hexval()[2:]  # '0x27ae60' → '27ae60'
    li_str = f"{li_val:.1f}".replace('.', ',')

    story.append(_section_header('INDICE DE APRENDIZAGEM'))
    story.append(Spacer(1, 0.15 * cm))

    ia_table = Table([[
        Paragraph(
            f"<font size='28' color='#{li_hex}'><b>{li_str}%</b></font>",
            ParagraphStyle('ia_v', parent=S['body'], alignment=TA_CENTER),
        ),
        Paragraph(
            f"<b>{satisfactory}</b> de <b>{metrics['total_students']}</b> alunos "
            f"em nivel <b>Avancado</b> ou <b>Adequado</b>.<br/><br/>"
            f"Formula: (Avancado + Adequado) / Total de alunos",
            ParagraphStyle('ia_s', parent=S['body'], fontSize=9,
                           textColor=C_MUTED, leading=14),
        ),
    ]], colWidths=[usable_w * 0.28, usable_w * 0.72])
    ia_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_LIGHT_BG),
        ('BOX', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEAFTER', (0, 0), (0, -1), 0.5, C_BORDER),
    ]))
    story.append(ia_table)
    story.append(Spacer(1, 0.4 * cm))

    # ══════════════════════════════════════════════════════════════════════
    # RODAPÉ
    # ══════════════════════════════════════════════════════════════════════
    story.append(HRFlowable(width='100%', thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, 0.1 * cm))
    story.append(Paragraph(
        f'Relatório gerado em {today}  ·  MAPA — Monitoramento de Aprendizagem Pedagógica Anual',
        S['footer'],
    ))

    doc.build(story)
    return buf.getvalue()
