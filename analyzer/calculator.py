"""
Calcula todas as métricas MAPA a partir dos dados extraídos do PDF.

Critérios:
  - Média geral da turma: soma das médias por disciplina / nº disciplinas
  - Média por disciplina: soma das notas / nº alunos
  - Domínio por níveis (por aluno, pela média geral do aluno):
      Avançado  8,0 – 10
      Adequado  6,0 – 7,9
      Básico    4,0 – 5,9
      Crítico   0   – 3,9
  - Taxa de risco: alunos com ≥1 disciplina em Básico ou Crítico
  - Disciplinas críticas: disciplinas com menor média da turma
  - Índice de aprendizagem: (Avançado + Adequado) / total × 100
"""

from .parser import SUBJECT_NAMES


LEVELS = [
    ('avancado', 'Avançado',  8.0, 10.0, '#27ae60'),
    ('adequado', 'Adequado',  6.0,  7.9, '#f39c12'),
    ('basico',   'Básico',    4.0,  5.9, '#e67e22'),
    ('critico',  'Crítico',   0.0,  3.9, '#e74c3c'),
]


def get_level(grade: float | None) -> dict | None:
    """Retorna o dict de nível para uma nota, ou None se a nota for None."""
    if grade is None:
        return None
    for key, label, low, high, color in LEVELS:
        if grade >= low:
            return {'key': key, 'label': label, 'color': color}
    return {'key': 'critico', 'label': 'Crítico', 'color': '#e74c3c'}


def calculate(data: dict) -> dict:
    students = data['students']
    subjects = data['subjects']
    n = len(students)

    # ── Médias por disciplina ─────────────────────────────────────────────
    subject_averages = []
    for idx, code in enumerate(subjects):
        valid = [s['grades'][idx] for s in students if s['grades'][idx] is not None]
        avg = round(sum(valid) / len(valid), 1) if valid else None
        subject_averages.append({
            'code': code,
            'name': SUBJECT_NAMES.get(code, code),
            'average': avg,
            'level': get_level(avg),
        })

    # ── Média geral da turma ──────────────────────────────────────────────
    valid_avgs = [s['average'] for s in subject_averages if s['average'] is not None]
    class_average = round(sum(valid_avgs) / len(valid_avgs), 1) if valid_avgs else None

    # ── Média individual de cada aluno ────────────────────────────────────
    enriched_students = []
    for s in students:
        valid = [g for g in s['grades'] if g is not None]
        avg = round(sum(valid) / len(valid), 1) if valid else None
        enriched_students.append({
            **s,
            'average': avg,
            'level': get_level(avg),
        })

    # ── Distribuição de níveis (pela média geral do aluno) ────────────────
    level_counts = {'avancado': 0, 'adequado': 0, 'basico': 0, 'critico': 0}
    for s in enriched_students:
        if s['level']:
            level_counts[s['level']['key']] += 1

    level_distribution = []
    for key, label, *_, color in LEVELS:
        count = level_counts[key]
        pct = round(count / n * 100, 1) if n else 0
        level_distribution.append({
            'key': key,
            'label': label,
            'color': color,
            'count': count,
            'pct': pct,
        })

    # ── Alunos em risco (≥1 disciplina em Básico ou Crítico) ─────────────
    at_risk = []
    for s in enriched_students:
        critical_subjects = []
        for idx, code in enumerate(subjects):
            g = s['grades'][idx]
            if g is not None and g < 6.0:
                critical_subjects.append({
                    'code': code,
                    'name': SUBJECT_NAMES.get(code, code),
                    'grade': g,
                    'level': get_level(g),
                })
        if critical_subjects:
            at_risk.append({
                'name': s['name'],
                'ra': s['ra'],
                'average': s['average'],
                'level': s['level'],
                'critical_subjects': critical_subjects,
            })

    risk_count = len(at_risk)
    risk_pct = round(risk_count / n * 100, 1) if n else 0

    # ── Disciplinas críticas (ordenadas da menor para maior média) ────────
    critical_subjects_ranked = sorted(
        [s for s in subject_averages if s['average'] is not None],
        key=lambda x: x['average'],
    )

    # ── Índice de aprendizagem ────────────────────────────────────────────
    satisfactory = level_counts['avancado'] + level_counts['adequado']
    learning_index = round(satisfactory / n * 100, 1) if n else 0

    # ── Frequência média da turma ─────────────────────────────────────────
    freq_media = None
    total_aulas = data.get('total_aulas')
    if total_aulas and total_aulas > 0 and n > 0:
        total_faltas_turma = sum(s.get('total_faltas') or 0 for s in students)
        total_possivel = n * total_aulas
        presencas = total_possivel - total_faltas_turma
        freq_media = round(presencas / total_possivel * 100, 1)

    return {
        'total_students': n,
        'class_average': class_average,
        'freq_media': freq_media,
        'class_level': get_level(class_average),
        'subject_averages': subject_averages,
        'level_distribution': level_distribution,
        'at_risk': at_risk,
        'risk_count': risk_count,
        'risk_pct': risk_pct,
        'critical_subjects_ranked': critical_subjects_ranked,
        'learning_index': learning_index,
        'students': enriched_students,
    }
