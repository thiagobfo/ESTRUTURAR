"""
Extrator de dados de relatório TQS - ficheiro .lst
Extrai: cabeçalho, quantitativos (vigas/pilares/lajes), avisos e lajes nervuradas.
Exporta os resultados para ficheiros CSV na pasta ./output/
"""

import os
import re
import sys

import ezdxf
from ezdxf import enums

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, Image, HRFlowable
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# ---------------------------------------------------------------------------
# Padrões de número: aceita tanto  2.68  como  .10  (sem dígito antes do ponto)
# ---------------------------------------------------------------------------
_NUM = r"(\d*\.?\d+)"


def carregar_ficheiro(caminho: str) -> list[str]:
    """Lê o ficheiro .lst testando encodings comuns até não haver erros."""
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            with open(caminho, encoding=enc) as f:
                return f.readlines()
        except UnicodeDecodeError:
            continue
    with open(caminho, encoding="utf-8", errors="replace") as f:
        return f.readlines()


# ---------------------------------------------------------------------------
# Cabeçalho
# ---------------------------------------------------------------------------

def extrair_cabecalho(linhas: list[str]) -> dict:
    cab = {}
    for linha in linhas:
        s = linha.strip()
        if re.match(r"Edif[íi]cio \.*", s):
            cab["edificio"] = re.split(r"\.{2,}", s, maxsplit=1)[-1].strip()
        elif re.match(r"Planta \.*", s):
            cab["pavimento"] = re.split(r"\.{2,}", s, maxsplit=1)[-1].strip()
        elif re.match(r"T[íi]tulo geral\.*", s):
            cab["titulo_geral"] = re.split(r"\.{2,}", s, maxsplit=1)[-1].strip()
        elif re.match(r"T[íi]tulo planta[\. ]+", s):
            cab["titulo_planta"] = re.split(r"[\. ]{2,}", s, maxsplit=1)[-1].strip()
    return cab


# ---------------------------------------------------------------------------
# Quantitativos
# ---------------------------------------------------------------------------

# Viga: 5 colunas numéricas
_PAT_VIGA = re.compile(
    r"^\s+(V\w+)\s+" + r"\s+".join([_NUM] * 5) + r"\s*$"
)
# Pilar: 4 colunas numéricas
_PAT_PILAR = re.compile(
    r"^\s+(P\w+)\s+" + r"\s+".join([_NUM] * 4) + r"\s*$"
)
# Laje: pelo menos 3 colunas numéricas (pode ter texto após)
_PAT_LAJE = re.compile(
    r"^\s+(L\w+)\s+" + r"\s+".join([_NUM] * 3)
)


def extrair_quantitativos(linhas: list[str]) -> dict:
    vigas, pilares, lajes = [], [], []

    for linha in linhas:
        m = _PAT_VIGA.match(linha)
        if m:
            vigas.append({
                "elemento":           m.group(1),
                "area_estruturada_m2": float(m.group(2)),
                "area_formas_m2":      float(m.group(3)),
                "vol_concreto_m3":     float(m.group(4)),
                "comp_linear_m":       float(m.group(5)),
                "comp_medio_vaos_m":   float(m.group(6)),
            })
            continue

        m = _PAT_PILAR.match(linha)
        if m:
            pilares.append({
                "elemento":           m.group(1),
                "area_estruturada_m2": float(m.group(2)),
                "area_formas_m2":      float(m.group(3)),
                "vol_concreto_m3":     float(m.group(4)),
                "vol_topo_m3":         float(m.group(5)),
            })
            continue

        m = _PAT_LAJE.match(linha)
        if m:
            lajes.append({
                "elemento":           m.group(1),
                "area_estruturada_m2": float(m.group(2)),
                "area_formas_m2":      float(m.group(3)),
                "vol_concreto_m3":     float(m.group(4)),
            })

    return {"vigas": vigas, "pilares": pilares, "lajes": lajes}


# ---------------------------------------------------------------------------
# Avisos
# ---------------------------------------------------------------------------

_PAT_AVISO = re.compile(r"^\*\*\*(\d+)\s+AVISO:\s+(.+)$")


def extrair_avisos(linhas: list[str]) -> list[dict]:
    avisos = []
    for linha in linhas:
        m = _PAT_AVISO.match(linha.rstrip())
        if m:
            avisos.append({
                "numero":   int(m.group(1)),
                "mensagem": m.group(2).strip(),
            })
    return avisos


# ---------------------------------------------------------------------------
# Lajes nervuradas
# ---------------------------------------------------------------------------

_PAT_LN = re.compile(
    r"^\s+(\d+)\s+" + r"\s+".join([_NUM] * 10) + r"\s*$"
)


def extrair_lajes_nervuradas(linhas: list[str]) -> list[dict]:
    registos = []
    em_secao = False
    for linha in linhas:
        if "Lajes Nervuradas" in linha:
            em_secao = True
            continue
        if em_secao:
            m = _PAT_LN.match(linha)
            if m:
                registos.append({
                    "laje":    int(m.group(1)),
                    "HN_cm":   float(m.group(2)),
                    "CAPA_cm": float(m.group(3)),
                    "LNX_cm":  float(m.group(4)),
                    "DNX_cm":  float(m.group(5)),
                    "LNY_cm":  float(m.group(6)),
                    "DNY_cm":  float(m.group(7)),
                    "ENCH":    float(m.group(8)),
                    "HmedC_cm":float(m.group(9)),
                    "HmedE_cm":float(m.group(10)),
                    "PP_tf_m2":float(m.group(11)),
                })
    return registos


# ---------------------------------------------------------------------------
# Totais por tipo de elemento
# ---------------------------------------------------------------------------

def calcular_totais(quant: dict) -> list[dict]:
    """Soma vol_concreto_m3 e area_formas_m2 por tipo de elemento."""
    totais = []
    mapa = [
        ("VIGA",  quant["vigas"]),
        ("LAJE",  quant["lajes"]),
        ("PILAR", quant["pilares"]),
    ]
    total_concreto = 0.0
    total_formas   = 0.0
    for nome, lista in mapa:
        concreto = sum(e["vol_concreto_m3"] for e in lista)
        formas   = sum(e["area_formas_m2"]  for e in lista)
        totais.append({"elemento": nome, "concreto_m3": concreto, "formas_m2": formas})
        total_concreto += concreto
        total_formas   += formas
    totais.append({"elemento": "TOTAL", "concreto_m3": total_concreto, "formas_m2": total_formas})
    return totais


# ---------------------------------------------------------------------------
# Exportação DXF
# ---------------------------------------------------------------------------

# Configurações visuais
_ALT_TEXTO   = 2.0   # altura do texto (mm / unidades DXF)
_ALT_TITULO  = 3.0   # altura do título de cada tabela
_ALT_LINHA   = 7.0   # altura de cada linha da tabela
_ESP_COL     = 3.0   # espaçamento interno horizontal por célula
_COR_HEADER  = 3     # verde
_COR_TITULO  = 5     # azul
_COR_BORDER  = 7     # branco / preto
_COR_TEXTO   = 7

# Configurações da tabela-resumo
_ALT_LINHA_RESUMO  = 20.0
_ALT_TEXTO_RESUMO  = 5.5
_LARG_COL_RESUMO   = [70.0, 90.0, 85.0]   # Elementos | Concreto | Formas


def _desenhar_tabela_resumo(
    msp,
    titulo: str,
    linhas_dados: list[list[str]],
    x0: float,
    y0: float,
) -> float:
    """
    Tabela de resumo com células altas, texto centrado e sem fundo.
    Última linha (TOTAL) é destacada em negrito via texto maior.
    """
    cabecalhos = ["ELEMENTOS", "CONCRETO (m3)", "FORMAS (m2)"]
    larguras   = _LARG_COL_RESUMO
    largura_total = sum(larguras)
    y = y0

    # Título
    msp.add_text(
        titulo,
        dxfattribs={"insert": (x0, y - _ALT_TITULO), "height": _ALT_TITULO + 1,
                    "color": _COR_TITULO, "layer": "TITULOS"},
    )
    y -= _ALT_TITULO + 4

    def _celula_centrada(texto, cx, cy, altura, layer, cor=7):
        # Largura estimada por caracter ≈ 0.65 * altura (fonte DXF padrão)
        x_start = cx - len(texto) * altura * 0.325
        y_start = cy - altura * 0.35  # baseline abaixo do centro vertical
        msp.add_text(
            texto,
            dxfattribs={
                "insert": (x_start, y_start),
                "height": altura,
                "color": cor,
                "layer": layer,
            },
        )

    def _linha_h(yy):
        msp.add_line((x0, yy), (x0 + largura_total, yy),
                     dxfattribs={"color": _COR_BORDER, "layer": "BORDAS"})

    # Borda superior
    _linha_h(y)

    # Cabeçalho
    x = x0
    for i, cab in enumerate(cabecalhos):
        cx = x + larguras[i] / 2
        cy = y - _ALT_LINHA_RESUMO / 2
        _celula_centrada(cab, cx, cy, _ALT_TEXTO_RESUMO, "HEADER_TEXTO", cor=3)
        x += larguras[i]
    y -= _ALT_LINHA_RESUMO
    _linha_h(y)

    # Linhas de dados
    for linha in linhas_dados:
        is_total = linha[0].upper() == "TOTAL"
        alt = _ALT_TEXTO_RESUMO * 1.15 if is_total else _ALT_TEXTO_RESUMO
        cor = 5 if is_total else _COR_TEXTO
        x = x0
        for i, cel in enumerate(linha):
            cx = x + larguras[i] / 2
            cy = y - _ALT_LINHA_RESUMO / 2
            _celula_centrada(cel, cx, cy, alt, "DADOS", cor=cor)
            x += larguras[i]
        y -= _ALT_LINHA_RESUMO
        _linha_h(y)

    # Bordas verticais
    y_topo = y0 - _ALT_TITULO - 4
    x = x0
    for larg in larguras:
        msp.add_line((x, y_topo), (x, y),
                     dxfattribs={"color": _COR_BORDER, "layer": "BORDAS"})
        x += larg
    msp.add_line((x, y_topo), (x, y),
                 dxfattribs={"color": _COR_BORDER, "layer": "BORDAS"})

    return y - 20


def _largura_colunas(cabecalhos: list[str], linhas_dados: list[list[str]]) -> list[float]:
    """Calcula a largura de cada coluna com base no conteúdo."""
    larguras = [len(h) * _ALT_TEXTO * 0.7 + _ESP_COL * 2 for h in cabecalhos]
    for linha in linhas_dados:
        for i, cel in enumerate(linha):
            w = len(cel) * _ALT_TEXTO * 0.65 + _ESP_COL * 2
            if w > larguras[i]:
                larguras[i] = w
    return larguras


def _desenhar_tabela(
    msp,
    titulo: str,
    cabecalhos: list[str],
    linhas_dados: list[list[str]],
    x0: float,
    y0: float,
) -> float:
    """
    Desenha uma tabela em DXF a partir de (x0, y0) — canto superior esquerdo.
    Devolve a coordenada Y do fim da tabela (para encadear tabelas).
    """
    larguras = _largura_colunas(cabecalhos, linhas_dados)
    largura_total = sum(larguras)
    y = y0

    # --- Título ---
    msp.add_text(
        titulo,
        dxfattribs={
            "insert": (x0, y - _ALT_TITULO),
            "height": _ALT_TITULO,
            "color": _COR_TITULO,
            "layer": "TITULOS",
        },
    )
    y -= _ALT_TITULO + 2

    # --- Cabeçalho ---
    x = x0
    for i, cab in enumerate(cabecalhos):
        # fundo do cabeçalho (hachura via sólido)
        msp.add_solid(
            [
                (x, y),
                (x + larguras[i], y),
                (x, y - _ALT_LINHA),
                (x + larguras[i], y - _ALT_LINHA),
            ],
            dxfattribs={"color": _COR_HEADER, "layer": "HEADER_BG"},
        )
        msp.add_text(
            cab,
            dxfattribs={
                "insert": (x + _ESP_COL, y - _ALT_LINHA + (_ALT_LINHA - _ALT_TEXTO) / 2),
                "height": _ALT_TEXTO,
                "color": 0,  # preto sobre fundo verde
                "layer": "HEADER_TEXTO",
            },
        )
        x += larguras[i]
    # borda inferior do cabeçalho
    msp.add_line((x0, y - _ALT_LINHA), (x0 + largura_total, y - _ALT_LINHA),
                 dxfattribs={"color": _COR_BORDER, "layer": "BORDAS"})
    y -= _ALT_LINHA

    # --- Linhas de dados ---
    for idx_linha, linha in enumerate(linhas_dados):
        cor_fundo = 9 if idx_linha % 2 == 0 else 255  # alternância cinza / branco
        x = x0
        for i, cel in enumerate(linha):
            msp.add_text(
                cel,
                dxfattribs={
                    "insert": (x + _ESP_COL, y - _ALT_LINHA + (_ALT_LINHA - _ALT_TEXTO) / 2),
                    "height": _ALT_TEXTO,
                    "color": _COR_TEXTO,
                    "layer": "DADOS",
                },
            )
            x += larguras[i]
        msp.add_line((x0, y - _ALT_LINHA), (x0 + largura_total, y - _ALT_LINHA),
                     dxfattribs={"color": _COR_BORDER, "layer": "BORDAS"})
        y -= _ALT_LINHA

    # --- Bordas verticais ---
    x = x0
    y_topo = y0 - _ALT_TITULO - 2
    for larg in larguras:
        msp.add_line((x, y_topo), (x, y),
                     dxfattribs={"color": _COR_BORDER, "layer": "BORDAS"})
        x += larg
    msp.add_line((x, y_topo), (x, y),
                 dxfattribs={"color": _COR_BORDER, "layer": "BORDAS"})
    # borda superior
    msp.add_line((x0, y_topo), (x0 + largura_total, y_topo),
                 dxfattribs={"color": _COR_BORDER, "layer": "BORDAS"})

    return y - 15  # espaço entre tabelas


def exportar_dxf(dados: dict, pasta_saida: str, nome_base: str = "relatorio"):
    os.makedirs(pasta_saida, exist_ok=True)

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4  # milímetros
    msp = doc.modelspace()

    # Camadas
    for layer, cor in [
        ("TITULOS", _COR_TITULO),
        ("HEADER_BG", _COR_HEADER),
        ("HEADER_TEXTO", 7),
        ("BORDAS", _COR_BORDER),
        ("DADOS", 7),
    ]:
        doc.layers.add(layer, color=cor)

    y_cursor = 0.0

    # ---- Cabeçalho do relatório ----
    cab = dados["cabecalho"]
    for chave, valor in cab.items():
        msp.add_text(
            f"{chave.replace('_', ' ').upper()}: {valor}",
            dxfattribs={"insert": (0, y_cursor), "height": _ALT_TITULO, "color": _COR_TITULO, "layer": "TITULOS"},
        )
        y_cursor -= _ALT_TITULO + 2
    y_cursor -= 10

    # ---- Tabela de Resumo (totais por tipo) ----
    totais = calcular_totais(dados["quantitativos"])
    linhas_resumo = [
        [t["elemento"], f"{t['concreto_m3']:.2f}", f"{t['formas_m2']:.2f}"]
        for t in totais
    ]
    y_cursor = _desenhar_tabela_resumo(msp, "RESUMO DE QUANTITATIVOS", linhas_resumo, 0, y_cursor)

    caminho = os.path.join(pasta_saida, f"{nome_base}.dxf")
    doc.saveas(caminho)
    print(f"  -> {caminho}")



# ---------------------------------------------------------------------------
# Exportação PDF
# ---------------------------------------------------------------------------

def exportar_pdf(dados: dict, pasta_saida: str, nome_base: str = "relatorio"):
    os.makedirs(pasta_saida, exist_ok=True)
    caminho = os.path.join(pasta_saida, f"{nome_base}.pdf")

    # Cores Estruturar
    COR_LARANJA_ESCURO  = colors.HexColor("#C0390B")  # laranja escuro do logo
    COR_LARANJA_MEDIO   = colors.HexColor("#E67E22")  # laranja médio
    COR_LARANJA_CLARO   = colors.HexColor("#FAD7A0")  # laranja claro (linha TOTAL)
    COR_CINZA_LINHA     = colors.HexColor("#FEF9F5")  # fundo alternado quase branco
    COR_TEXTO_ESCURO    = colors.HexColor("#1C1C1C")

    doc = SimpleDocTemplate(
        caminho,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=15 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    estilo_info = ParagraphStyle(
        "info", parent=styles["Normal"],
        fontSize=9, textColor=COR_TEXTO_ESCURO, leading=13,
        alignment=TA_LEFT,
    )
    estilo_titulo_tabela = ParagraphStyle(
        "titulo_tab", parent=styles["Heading2"],
        fontSize=13, textColor=COR_LARANJA_ESCURO,
        fontName="Helvetica-Bold", spaceAfter=3 * mm,
        alignment=TA_LEFT,
    )

    story = []

    # ---- Logo + cabeçalho lado a lado ----
    logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
    cab = dados["cabecalho"]
    linhas_cab = [
        f"<b>PAVIMENTO:</b> {cab.get('pavimento', '')}",
        f"<b>EDIFÍCIO:</b> {cab.get('titulo_geral', '')}",
    ]
    col_cab = [[Paragraph(l, estilo_info)] for l in linhas_cab]
    tabela_info_data = [[cel[0]] for cel in col_cab]

    if os.path.exists(logo_path):
        logo_img = Image(logo_path, width=40 * mm, height=40 * mm, kind="proportional")
        cabecalho_data = [[
            [Paragraph(l, estilo_info) for l in linhas_cab],
            logo_img,
        ]]
        tabela_cab = Table(cabecalho_data, colWidths=[125 * mm, 45 * mm])
        tabela_cab.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",         (1, 0), (1, 0),   "RIGHT"),
            ("RIGHTPADDING",  (1, 0), (1, 0),   0),
        ]))
    else:
        # Sem logo: só texto
        tabela_cab = Table(
            [[Paragraph(l, estilo_info)] for l in linhas_cab],
            colWidths=[170 * mm],
        )

    story.append(tabela_cab)
    story.append(HRFlowable(width="100%", thickness=2, color=COR_LARANJA_ESCURO,
                             spaceAfter=6 * mm, spaceBefore=4 * mm))

    # ---- Tabela de resumo ----
    totais = calcular_totais(dados["quantitativos"])

    col_w = [60 * mm, 55 * mm, 55 * mm]
    tabela_dados = [["ELEMENTOS", "CONCRETO (m³)", "FORMAS (m²)"]]
    for t in totais:
        tabela_dados.append([
            t["elemento"],
            f"{t['concreto_m3']:.2f}",
            f"{t['formas_m2']:.2f}",
        ])

    n_linhas = len(tabela_dados)
    idx_total = n_linhas - 1

    # Fundo alternado nas linhas de dados (exceto TOTAL)
    row_bg = []
    for i in range(1, idx_total):
        bg = COR_CINZA_LINHA if i % 2 == 0 else colors.white
        row_bg.append(("BACKGROUND", (0, i), (-1, i), bg))

    estilo_tabela = TableStyle([
        # Cabeçalho
        ("BACKGROUND",    (0, 0), (-1, 0),          COR_LARANJA_ESCURO),
        ("TEXTCOLOR",     (0, 0), (-1, 0),          colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),          "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),          11),
        ("ALIGN",         (0, 0), (-1, -1),         "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1),         "MIDDLE"),
        # Dados
        ("FONTNAME",      (0, 1), (-1, idx_total - 1), "Helvetica"),
        ("FONTSIZE",      (0, 1), (-1, idx_total - 1), 10),
        ("TEXTCOLOR",     (0, 1), (-1, idx_total - 1), COR_TEXTO_ESCURO),
        # Linha TOTAL
        ("BACKGROUND",    (0, idx_total), (-1, idx_total), COR_LARANJA_CLARO),
        ("FONTNAME",      (0, idx_total), (-1, idx_total), "Helvetica-Bold"),
        ("FONTSIZE",      (0, idx_total), (-1, idx_total), 11),
        ("TEXTCOLOR",     (0, idx_total), (-1, idx_total), COR_LARANJA_ESCURO),
        # Grelha
        ("GRID",          (0, 0), (-1, -1),         0.5, COR_LARANJA_MEDIO),
        ("LINEBELOW",     (0, 0), (-1, 0),          1.5, COR_LARANJA_ESCURO),
        ("TOPPADDING",    (0, 0), (-1, -1),         7),
        ("BOTTOMPADDING", (0, 0), (-1, -1),         7),
        *row_bg,
    ])

    tabela = Table(tabela_dados, colWidths=col_w, repeatRows=1)
    tabela.setStyle(estilo_tabela)

    story.append(Paragraph("RESUMO DE QUANTITATIVOS", estilo_titulo_tabela))
    story.append(tabela)

    doc.build(story)
    print(f"  -> {caminho}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    caminho_lst = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(os.path.dirname(__file__), "relatorio.lst")
    )

    if not os.path.exists(caminho_lst):
        print(f"Ficheiro não encontrado: {caminho_lst}")
        sys.exit(1)

    print(f"A processar: {caminho_lst}\n")
    linhas = carregar_ficheiro(caminho_lst)

    cabecalho         = extrair_cabecalho(linhas)
    quant             = extrair_quantitativos(linhas)
    avisos            = extrair_avisos(linhas)
    lajes_nervuradas  = extrair_lajes_nervuradas(linhas)

    print("Cabeçalho:")
    for k, v in cabecalho.items():
        print(f"  {k}: {v}")

    print(f"\nQuantitativos:")
    print(f"  Vigas:   {len(quant['vigas'])} elementos")
    print(f"  Pilares: {len(quant['pilares'])} elementos")
    print(f"  Lajes:   {len(quant['lajes'])} elementos")
    print(f"\nLajes nervuradas: {len(lajes_nervuradas)} registos")
    print(f"Avisos:           {len(avisos)}")

    dados = {
        "cabecalho":        cabecalho,
        "quantitativos":    quant,
        "avisos":           avisos,
        "lajes_nervuradas": lajes_nervuradas,
    }

    pasta_saida = os.path.join(os.path.dirname(__file__), "output")
    nome_base = os.path.splitext(os.path.basename(caminho_lst))[0]
    print(f"\nA exportar para: {pasta_saida}/")
    exportar_dxf(dados, pasta_saida, nome_base)
    exportar_pdf(dados, pasta_saida, nome_base)
    print("\nConcluído.")


if __name__ == "__main__":
    main()
