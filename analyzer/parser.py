import re
from pypdf import PdfReader

SUBJECT_NAMES = {
    'ART': 'Arte',
    'C': 'Ciências',
    'EF': 'Educação Física',
    'ER': 'Ensino Religioso',
    'G': 'Geografia',
    'H': 'História',
    'ING': 'Língua Inglesa',
    'LP': 'Língua Portuguesa',
    'M': 'Matemática',
}

DEFAULT_SUBJECTS = ['ART', 'C', 'EF', 'ER', 'G', 'H', 'ING', 'LP', 'M']

INACTIVE_KEYWORDS = ['transferência', 'cancelado', 'transferencia']

BIMESTER_LABELS = {
    '1': '1º Bimestre',
    '2': '2º Bimestre',
    '3': '3º Bimestre',
    '4': '4º Bimestre',
}

# Linhas de cabeçalho/rodapé a ignorar
SKIP_PATTERNS = re.compile(
    r'^(Escola\s|Goiás|Quadro\s|Turma:|Períodos|Nº\s*-\s*Nome|Legenda:|Arte\s*\(|'
    r'Ciências\s*\(|Educação\s*Física|Ensino\s*Religioso|Geografia\s*\(|'
    r'História\s*\(|Língua\s*|Matemática\s*\(|\d{2}/\d{2}/\d{4}\s+\d{2}:)',
    re.IGNORECASE
)

# Linha de cabeçalho de coluna "N F" ou código de disciplina isolado
COLUMN_HEADER = re.compile(r'^(N\s+F|[A-Z]{1,4})$')

# Linha de aluno: "1 - Nome Sobrenome (RA)"
STUDENT_LINE = re.compile(r'^(\d+)\s*-\s*(.+?)\s*\((\d+)\)\s*(-\s*(.+))?$')

# Linha de dados de bimestre/ano letivo
PERIOD_START = re.compile(
    r'^(\d[ºo°]\s*Bimestre|\d{4}\s*-\s*Ano\s*Letivo)\s+[\d\-]',
    re.IGNORECASE
)


def _parse_float(token: str):
    """Converte token de nota para float. Retorna None para '--'."""
    token = token.strip()
    if token in ('--', '-'):
        return None
    try:
        return float(token.replace(',', '.'))
    except ValueError:
        return None


def _extract_grades(line: str, prefix: str):
    """
    Extrai as notas e total de faltas de uma linha de bimestre.
    Formato: "{prefix} nota falta nota falta ... total_faltas"
    As notas estão nas posições pares (0, 2, 4, ...).
    Retorna (grades, total_faltas).
    """
    data = line[len(prefix):].strip()
    if data.startswith('*'):
        data = data[1:].strip()
    tokens = data.split()
    grades = []
    i = 0
    while i < len(tokens) - 1:  # último token é total de faltas
        grade = _parse_float(tokens[i])
        grades.append(grade)
        i += 2  # pula nota + falta

    total_faltas = None
    if tokens:
        try:
            total_faltas = int(tokens[-1])
        except (ValueError, TypeError):
            f = _parse_float(tokens[-1])
            if f is not None:
                total_faltas = int(round(f))

    return (grades if grades else None), total_faltas


def _extract_total_aulas(all_text: str) -> int | None:
    """Extrai o total de aulas (períodos) do cabeçalho do PDF."""
    for line in all_text.split('\n'):
        stripped = line.strip()
        if re.match(r'^Per[ií]odos', stripped, re.IGNORECASE):
            numbers = re.findall(r'\d+', stripped)
            if numbers:
                return int(numbers[0])
    return None


def _extract_meta(all_text: str):
    """Extrai escola, nome da turma e ano letivo do cabeçalho."""
    school = ''
    class_name = ''
    year = '2026'

    for line in all_text.split('\n'):
        line = line.strip()
        if not school and line.lower().startswith('escola'):
            school = line
        m = re.search(r'Turma:\s*(.+?)\s+Ano\s+Letivo:\s*(\d{4})', line)
        if m:
            class_name = m.group(1).strip()
            year = m.group(2)
        if school and class_name:
            break

    return school, class_name, year


def _extract_subjects(all_text: str):
    """
    Extrai os códigos de disciplinas do cabeçalho da tabela.
    Busca a linha 'Nº - Nome (RA)' e coleta os códigos que aparecem
    entre as linhas 'N F' até 'Faltas'.
    """
    subjects = []
    lines = all_text.split('\n')
    in_header = False

    for line in lines:
        stripped = line.strip()

        if 'Nº' in stripped and 'Nome' in stripped and '(RA)' in stripped:
            if in_header:
                # segunda ocorrência = repete em nova página, para
                break
            in_header = True
            # primeiro código pode estar na mesma linha
            rest = re.split(r'\(RA\)', stripped)[-1].strip()
            if re.match(r'^[A-Z]{1,4}$', rest):
                subjects.append(rest)
            continue

        if in_header:
            if 'Faltas' in stripped:
                break
            if re.match(r'^[A-Z]{1,4}$', stripped) and stripped not in ('N', 'F'):
                subjects.append(stripped)

    return subjects if len(subjects) >= 3 else DEFAULT_SUBJECTS


def _build_prefix(bimester: str, year: str) -> str:
    if bimester in BIMESTER_LABELS:
        return BIMESTER_LABELS[bimester]
    return f'{year} - Ano Letivo'


def _detect_bimester(all_text: str) -> str:
    """
    Detecta o bimestre ativo no PDF.
    O sistema escolar marca o período selecionado com asterisco, ex: "2º Bimestre*".
    Se não houver asterisco, usa o último bimestre com notas lançadas.
    """
    fallback = None
    for raw_line in all_text.split('\n'):
        line = raw_line.strip()
        marker = re.match(r'^([1-4])\D*Bimestre\s*\*', line, re.IGNORECASE)
        if marker:
            return marker.group(1)

        period = re.match(r'^([1-4])\D*Bimestre\s*\*?\s+(.+)$', line, re.IGNORECASE)
        if not period:
            continue

        tokens = period.group(2).split()
        grade_tokens = tokens[:-1:2]
        if any(_parse_float(token) is not None for token in grade_tokens):
            fallback = period.group(1)

    if fallback:
        return fallback
    raise ValueError('Não foi possível detectar automaticamente o bimestre do PDF.')


def extract_pdf_text(file_object) -> str:
    """Lê o PDF e extrai todo o texto de uma vez. Operação cara — chamar só 1x por arquivo."""
    reader = PdfReader(file_object)
    pages_text = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages_text.append(text)
    if not pages_text:
        raise ValueError("Não foi possível extrair texto do PDF.")
    all_text = '\n'.join(pages_text)
    if 'Quadro Comparativo' not in all_text and 'Bimestre' not in all_text:
        raise ValueError(
            "PDF não reconhecido. Envie o 'Quadro Comparativo de Notas/Faltas' "
            "exportado do sistema escolar."
        )
    return all_text


def parse_from_text(all_text: str, bimester: str = 'auto') -> dict:
    """
    Parseia os dados de um bimestre a partir do texto já extraído.
    Usar junto com extract_pdf_text() para evitar múltiplas leituras do PDF.
    Raises ValueError se o bimestre não tiver notas lançadas.
    """
    school, class_name, year = _extract_meta(all_text)
    if bimester in ('auto', '', None):
        bimester = _detect_bimester(all_text)
    subjects = _extract_subjects(all_text)
    prefix = _build_prefix(bimester, year)
    total_aulas = _extract_total_aulas(all_text)

    students = []
    current = None

    for raw_line in all_text.split('\n'):
        line = raw_line.strip()
        if not line:
            continue
        if SKIP_PATTERNS.match(line):
            continue
        if COLUMN_HEADER.match(line):
            continue

        m = STUDENT_LINE.match(line)
        if m:
            if current is not None:
                students.append(current)
            num_str = m.group(1)
            name = m.group(2).strip()
            ra = m.group(3).strip()
            status_text = (m.group(5) or '').strip().lower()
            active = not any(kw in status_text for kw in INACTIVE_KEYWORDS)
            current = {
                'num': int(num_str),
                'name': name,
                'ra': ra,
                'active': active,
                'grades': None,
            }
            continue

        if current and current['active'] and current['grades'] is None:
            if line.startswith(prefix):
                grades, total_faltas = _extract_grades(line, prefix)
                if grades and any(g is not None for g in grades):
                    while len(grades) < len(subjects):
                        grades.append(None)
                    current['grades'] = grades[:len(subjects)]
                    current['total_faltas'] = total_faltas

    if current is not None:
        students.append(current)

    active = [
        s for s in students
        if s['active']
        and s['grades'] is not None
        and any(g is not None for g in s['grades'])
    ]

    if not active:
        raise ValueError(
            f"Nenhum aluno encontrado para o período '{prefix}'. "
            "Verifique se o bimestre selecionado possui notas lançadas."
        )

    bimester_label = BIMESTER_LABELS.get(bimester, f'Ano Letivo {year}')

    return {
        'school': school or 'Escola',
        'class_name': class_name or 'Turma',
        'year': year,
        'bimester': bimester,
        'bimester_label': bimester_label,
        'subjects': subjects,
        'students': active,
        'total_aulas': total_aulas,
    }


def parse_pdf(file_object, bimester: str = 'auto') -> dict:
    """Lê o PDF e parseia o bimestre indicado. Atalho para chamadas únicas."""
    all_text = extract_pdf_text(file_object)
    return parse_from_text(all_text, bimester)
