# MAPA — Contexto e Estrutura do Projeto

## O que é

Sistema Django que processa o PDF "Quadro Comparativo de Notas/Faltas" exportado do sistema escolar adventista e gera um relatório pedagógico chamado **MAPA** (Monitoramento de Aprendizagem Pedagógica Anual) em duas páginas A4, preenchido automaticamente com os dados da turma.

---

## Estrutura de arquivos

```
SITE FEFE/
├── manage.py                              # Entry point Django
├── requirements.txt                       # pypdf, Django, Pillow, reportlab
├── CONTEXTO_PROJETO.md                   # Este arquivo
├── mapa-puro-html-corrigido (1).html     # Template HTML do relatório (A4 × 2 páginas)
├── channels4_profile-removebg-preview.png # Logo Educação Adventista (fundo transparente)
│
├── mapa_project/                          # Config Django
│   ├── settings.py                        # Sem banco de dados, DEBUG=True, pt-br
│   ├── urls.py                            # Inclui analyzer.urls
│   └── wsgi.py
│
└── analyzer/                              # App principal
    ├── urls.py                            # 3 rotas (index, export-pdf, temp-report)
    ├── views.py                           # Lógica principal
    ├── parser.py                          # Extração de dados do PDF
    ├── calculator.py                      # Cálculo das métricas pedagógicas
    ├── pdf_generator.py                   # (legado — export usa Chrome headless)
    └── templates/analyzer/index.html     # Formulário de upload
```

---

## Fluxo completo

```
1. Professor acessa localhost:8000
2. Faz upload do PDF + seleciona bimestre + informa professor(a)
3. parser.py extrai: escola, turma, ano, 9 disciplinas, alunos com notas e faltas
4. calculator.py calcula: médias, níveis, risco, frequência, evolução
5. views._build_html_report() injeta os dados no template HTML via JavaScript
6. Browser exibe o relatório MAPA preenchido (2 páginas A4)
7. Botão "Exportar PDF" → Chrome headless gera PDF real para download
```

---

## URLs (analyzer/urls.py)

| Rota | View | Uso |
|------|------|-----|
| `/` | `index` | GET = formulário; POST = processa PDF |
| `/export-pdf/` | `export_pdf` | POST: recebe HTML, retorna PDF via Chrome headless |
| `/temp-report/<id>/` | `serve_temp_report` | Serve HTML temporário para Chrome headless acessar via HTTP |

---

## parser.py

Lê o PDF "Quadro Comparativo de Notas/Faltas" do sistema adventista.

**Retorno de `parse_pdf()`:**
```python
{
  'school':         'Escola Adventista de ...',
  'class_name':     '2º Ano - Tarde - B',
  'year':           '2026',
  'bimester':       '1',            # '1'|'2'|'3'|'4'
  'bimester_label': '1º Bimestre',
  'subjects':       ['ART','C','EF','ER','G','H','ING','LP','M'],
  'students': [
    {
      'num': 1,
      'name': 'João da Silva',
      'ra': '123456',
      'active': True,
      'grades': [8.5, 7.2, 9.0, 6.5, 7.8, 8.1, 8.9, 7.0, 8.3],
      'total_faltas': 4,   # total de faltas do aluno no bimestre
    }, ...
  ],
  'total_aulas': 50,  # Fixo em 50 aulas por bimestre (constante no calculator.py)
}
```

**Formato do PDF:**
- Linha de aluno: `1 - João da Silva (123456)`
- Linha de notas: `1º Bimestre nota1 falta1 nota2 falta2 ... total_faltas`
- Último token da linha de notas = total_faltas do aluno

---

## calculator.py

Calcula todas as métricas a partir dos dados do parser.

**Métricas retornadas:**

| Métrica | Descrição |
|---------|-----------|
| `total_students` | Número de alunos ativos com notas |
| `class_average` | Média geral da turma (média das médias por disciplina) |
| `freq_media` | Frequência % = [(alunos × 50) − Σfaltas] ÷ (alunos × 50) × 100. Total de aulas fixo = 50/bimestre |
| `subject_averages` | Lista com média por disciplina + nível |
| `level_distribution` | Contagem e % em Avançado/Adequado/Básico/Crítico |
| `at_risk` | Alunos com ≥1 disciplina abaixo de 6,0 |
| `risk_count` / `risk_pct` | Quantidade e % de alunos em risco |
| `learning_index` | (Avançado + Adequado) / total × 100 |
| `students` | Alunos enriquecidos com `average` e `level` |

**Níveis de aprendizagem:**
- Avançado: 8,0 – 10,0
- Adequado: 6,0 – 7,9
- Básico: 4,0 – 5,9
- Crítico: 0,0 – 3,9

---

## views.py — funções principais

### `index(request)`
Handler principal GET/POST. Recebe PDF(s), bimestre, nome do professor. Chama parser → calculator → `_build_html_report()`. Um PDF = exibe relatório direto. Vários PDFs = página de cards com links.

### `_build_html_report(data, metrics, teacher_name, evolution)`
Lê o template HTML do disco, injeta dados via script JavaScript (`data_map` → `data-key`). Também calcula status de alerta pedagógico (NORMAL / ATENÇÃO / ALERTA) e gera a tabela de alunos em risco para a seção 7 (campo `alerta-note`).

**Tabela de alunos em risco (seção 7):**
- Badge colorido com status e contagem no topo
- Tabela: Aluno | Mat. | Nota — uma linha por disciplina crítica por aluno
- Nota colorida conforme nível (vermelho = crítico, laranja = básico)
- Se nenhum aluno em risco → exibe "NORMAL" centralizado

### `_get_evolution(pdf_file, bimester, current_data)`
Relê o PDF com bimestre anterior e calcula crescimento/estabilidade/queda por aluno.

### `export_pdf(request)`
Recebe HTML via POST → strip scripts de extensões (Kaspersky) → armazena em `_pending_reports` → chama Chrome headless via `subprocess` para gerar PDF → retorna binary para download.

### `serve_temp_report(request, report_id)`
Serve HTML temporário (armazenado em memória) para Chrome headless buscar via HTTP (necessário porque `file:///` não funciona no headless em Windows).

---

## Template HTML — mapa-puro-html-corrigido (1).html

Arquivo grande (~1600 linhas). Duas páginas A4 dentro de `<section class="sheet">`.

**Página 1** (`id="page1"`):
- Header: MAPA + títulos + logo Adventista (PNG fundo transparente, base64 inline)
- Info: escola, turma, ano letivo, professor, data (3 colunas)
- Seção 1: Resumo geral (total alunos, média, IAT, alunos em risco, meta)
- Row2: Seções 2 (Distribuição nível), 3 (Disciplinas), 4 (Evolução)
- Row3 (3 colunas, 420px): Seções 5 (Engajamento — frequência calculada), 6 (Taxa de risco), 7 (Alerta pedagógico)
- Seção 7: coluna esquerda = condições de alerta + status; coluna direita = tabela de alunos em risco (nome, matéria, nota)
- Responsáveis: `.sign` com ícone + Professor(a) + Coordenação Pedagógica
- Footer com ciclo MAPA

**Página 2** (`id="page2"`):
- Header idêntico ao p1
- NotesGrid (2 colunas): 8 noteCards de observações (r1×2, r2×2, r3×2, r4×2=seções 7 e 8)
- Bottom2 (135px): Responsáveis igual ao da página 1 (`.sign`)
- Footer2 com ícones ANALISAR / INDICADORES / AÇÕES

**Campos data-key injetados pelo views.py:**
- Cabeçalho: `p1-escola`, `p1-turma`, `p1-ano`, `p1-prof`, `p1-d1/d2/d3`
- Resumo: `total-alunos`, `media-geral`, `iat`, `risco-qtd`, `risco-pct`, `meta`
- Níveis: `avancado`, `adequado`, `basico`, `critico` (+ `-q` para contagem)
- Disciplinas: `lp`, `mat`, `cie`, `his`, `geo`, `ing`, `ef`, `er`, `artes`
- Evolução: `cres`, `estab`, `queda`, `variacao`
- Engajamento: `freq` (calculado), `ativ`, `part` (manuais)
- Risco: `taxa-risco`, `qtd-risco`
- Alerta: `alerta-note` (HTML da tabela de alunos em risco)

**Injeção de dados:**
Todos os campos têm `data-key="nome-do-campo"`. O JavaScript gerado pelo `_build_html_report()` preenche via `el.innerHTML = D[key]`.

**CSS de impressão:**
```css
@media print { html { zoom: 73% } }
```
73% = 1024px × 73% ≈ 748px → cabe no A4 (794px largura). Cada sheet = 1536px × 73% ≈ 1122px = altura A4.

---

## Configurações Django (settings.py)

- `DEBUG = True`
- Sem banco de dados (stateless)
- Sem autenticação / admin
- `LANGUAGE_CODE = 'pt-br'`, `TIME_ZONE = 'America/Sao_Paulo'`
- Upload máximo: 20 MB
- `ALLOWED_HOSTS = ['*']`

---

## Como rodar

```bash
cd "c:\Users\Fabiano\Pictures\SITE FEFE"
python manage.py runserver
# Acesse http://localhost:8000
```

**Requisito:** Google Chrome instalado em `C:\Program Files\Google\Chrome\Application\chrome.exe` para exportar PDF.

---

## Repositório GitHub

`https://github.com/fabianopolone123/SITE_FEFE.git` — branch `main`

---

## Histórico de decisões importantes

| Decisão | Motivo |
|---------|--------|
| Chrome headless via HTTP (não file://) | `file:///` bloqueado no Windows + extensão Kaspersky injeta scripts |
| Scripts Kaspersky removidos antes do headless | Interfere na renderização do Chrome headless |
| zoom: 73% no CSS de impressão | 1024px × 73% ≈ 748px (< 794px A4); 1536px × 73% ≈ 1122px = altura A4 |
| Total de aulas fixo = 50 por bimestre | Definido pelo usuário; não extraído do PDF |
| total_faltas por aluno = último token da linha de notas | Formato do PDF adventista: `nota falta ... nota falta total_faltas` |
| Logo inline base64 | Evita problemas de caminho relativo no Chrome headless |
| Sem banco de dados | Sistema stateless — cada PDF gera relatório independente |
