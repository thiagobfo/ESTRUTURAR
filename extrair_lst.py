"""
Extrator de dados de relatório TQS - ficheiro .lst
Extrai: cabeçalho, quantitativos (vigas/pilares/lajes), avisos e lajes nervuradas.
Exporta os resultados para PDF e DXF na pasta ./output/
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
# Laje: 3 colunas numéricas + resto da linha (H no resto indica nervurada)
_PAT_LAJE = re.compile(
    r"^\s+(L\w+)\s+" + r"\s+".join([_NUM] * 3) + r"(.*)"
)


def extrair_quantitativos(linhas: list[str]) -> dict:
    vigas, pilares, lajes_macicas, lajes_nervuradas = [], [], [], []

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
            laje = {
                "elemento":           m.group(1),
                "area_estruturada_m2": float(m.group(2)),
                "area_formas_m2":      float(m.group(3)),
                "vol_concreto_m3":     float(m.group(4)),
            }
            if re.search(r'\bH\b', m.group(5)):
                lajes_nervuradas.append(laje)
            else:
                lajes_macicas.append(laje)

    return {"vigas": vigas, "pilares": pilares, "lajes_macicas": lajes_macicas, "lajes_nervuradas": lajes_nervuradas}


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
# Totais por pavimento
# ---------------------------------------------------------------------------

def _totais_quant(quant: dict) -> dict:
    """Returns per-type and total concrete/forms sums for one pavimento's quantitativos."""
    v_c  = sum(e["vol_concreto_m3"] for e in quant["vigas"])
    v_f  = sum(e["area_formas_m2"]  for e in quant["vigas"])
    p_c  = sum(e["vol_concreto_m3"] for e in quant["pilares"])
    p_f  = sum(e["area_formas_m2"]  for e in quant["pilares"])
    lm_c = sum(e["vol_concreto_m3"] for e in quant["lajes_macicas"])
    lm_f = sum(e["area_formas_m2"]  for e in quant["lajes_macicas"])
    ln_c = sum(e["vol_concreto_m3"] for e in quant["lajes_nervuradas"])
    ln_f = sum(e["area_formas_m2"]  for e in quant["lajes_nervuradas"])
    return {
        "vigas":            {"concreto": v_c,  "formas": v_f},
        "pilares":          {"concreto": p_c,  "formas": p_f},
        "lajes_macicas":    {"concreto": lm_c, "formas": lm_f},
        "lajes_nervuradas": {"concreto": ln_c, "formas": ln_f},
        "total":            {"concreto": v_c + p_c + lm_c + ln_c, "formas": v_f + p_f + lm_f + ln_f},
    }


# ---------------------------------------------------------------------------
# Exportação DXF
# ---------------------------------------------------------------------------

_ALT_TEXTO   = 2.0
_ALT_TITULO  = 3.0
_ALT_LINHA   = 7.0
_ESP_COL     = 3.0
_COR_HEADER  = 3     # verde
_COR_TITULO  = 5     # azul
_COR_BORDER  = 7
_COR_TEXTO   = 7


def _largura_colunas(cabecalhos: list[str], linhas_dados: list[list[str]]) -> list[float]:
    larguras = [len(h) * _ALT_TEXTO * 1.1 + _ESP_COL * 2 for h in cabecalhos]
    for linha in linhas_dados:
        for i, cel in enumerate(linha):
            w = len(cel) * _ALT_TEXTO * 1.1 + _ESP_COL * 2
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
    larguras = _largura_colunas(cabecalhos, linhas_dados)
    largura_total = sum(larguras)
    y = y0

    msp.add_text(
        titulo,
        dxfattribs={
            "insert": (x0, y - _ALT_TITULO),
            "height": _ALT_TITULO,
            "color": _COR_TITULO,
            "layer": "8",
        },
    )
    y -= _ALT_TITULO + 2

    x = x0
    for i, cab in enumerate(cabecalhos):
        msp.add_solid(
            [
                (x, y),
                (x + larguras[i], y),
                (x, y - _ALT_LINHA),
                (x + larguras[i], y - _ALT_LINHA),
            ],
            dxfattribs={"color": _COR_HEADER, "layer": "3"},
        )
        msp.add_text(
            cab,
            dxfattribs={
                "insert": (x + _ESP_COL, y - _ALT_LINHA + (_ALT_LINHA - _ALT_TEXTO) / 2),
                "height": _ALT_TEXTO,
                "color": 0,
                "layer": "8",
            },
        )
        x += larguras[i]
    msp.add_line((x0, y - _ALT_LINHA), (x0 + largura_total, y - _ALT_LINHA),
                 dxfattribs={"color": _COR_BORDER, "layer": "3"})
    y -= _ALT_LINHA

    for idx_linha, linha in enumerate(linhas_dados):
        is_total = str(linha[0]).upper() == "TOTAL"
        x = x0
        for i, cel in enumerate(linha):
            msp.add_text(
                cel,
                dxfattribs={
                    "insert": (x + _ESP_COL, y - _ALT_LINHA + (_ALT_LINHA - _ALT_TEXTO) / 2),
                    "height": _ALT_TEXTO * (1.1 if is_total else 1.0),
                    "color": _COR_TITULO if is_total else _COR_TEXTO,
                    "layer": "8",
                },
            )
            x += larguras[i]
        msp.add_line((x0, y - _ALT_LINHA), (x0 + largura_total, y - _ALT_LINHA),
                     dxfattribs={"color": _COR_BORDER, "layer": "3"})
        y -= _ALT_LINHA

    x = x0
    y_topo = y0 - _ALT_TITULO - 2
    for larg in larguras:
        msp.add_line((x, y_topo), (x, y),
                     dxfattribs={"color": _COR_BORDER, "layer": "3"})
        x += larg
    msp.add_line((x, y_topo), (x, y),
                 dxfattribs={"color": _COR_BORDER, "layer": "3"})
    msp.add_line((x0, y_topo), (x0 + largura_total, y_topo),
                 dxfattribs={"color": _COR_BORDER, "layer": "3"})

    return y - 15


def exportar_dxf(pavimentos: list[dict], edificio_nome: str, pasta_saida: str, nome_base: str = "relatorio"):
    os.makedirs(pasta_saida, exist_ok=True)

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    for layer, cor in [
        ("8", 7),
        ("3", 3),
    ]:
        doc.layers.add(layer, color=cor)

    y_cursor = 0.0

    msp.add_text(
        f"EDIFICIO: {edificio_nome}",
        dxfattribs={"insert": (0, y_cursor), "height": _ALT_TITULO + 1, "color": _COR_TITULO, "layer": "8"},
    )
    y_cursor -= _ALT_TITULO + 10

    cabecalhos = ["ELEMENTO", "Concreto (m3)", "Formas (m2)"]

    for pav in pavimentos:
        t = _totais_quant(pav["quantitativos"])
        linhas_dados = [
            ["VIGAS",              f"{t['vigas']['concreto']:.2f}",            f"{t['vigas']['formas']:.2f}"],
            ["PILARES",            f"{t['pilares']['concreto']:.2f}",          f"{t['pilares']['formas']:.2f}"],
            ["LAJES MACIÇAS",      f"{t['lajes_macicas']['concreto']:.2f}",    f"{t['lajes_macicas']['formas']:.2f}"],
            ["LAJES NERVURADAS",   f"{t['lajes_nervuradas']['concreto']:.2f}", f"{t['lajes_nervuradas']['formas']:.2f}"],
            ["TOTAL",              f"{t['total']['concreto']:.2f}",            f"{t['total']['formas']:.2f}"],
        ]
        y_cursor = _desenhar_tabela(msp, f"PAVIMENTO: {pav['nome']}", cabecalhos, linhas_dados, 0, y_cursor)

    caminho = os.path.join(pasta_saida, f"{nome_base}.dxf")
    doc.saveas(caminho)
    print(f"  -> {caminho}")


# ---------------------------------------------------------------------------
# Exportação PDF
# ---------------------------------------------------------------------------

def exportar_pdf(pavimentos: list[dict], edificio_nome: str, pasta_saida: str, nome_base: str = "relatorio"):
    os.makedirs(pasta_saida, exist_ok=True)
    caminho = os.path.join(pasta_saida, f"{nome_base}.pdf")

    COR_LARANJA_ESCURO = colors.HexColor("#C0390B")
    COR_LARANJA_MEDIO  = colors.HexColor("#E67E22")
    COR_LARANJA_CLARO  = colors.HexColor("#FAD7A0")
    COR_CINZA_LINHA    = colors.HexColor("#FEF9F5")
    COR_TEXTO_ESCURO   = colors.HexColor("#1C1C1C")

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
        fontSize=9, textColor=COR_TEXTO_ESCURO, leading=13, alignment=TA_LEFT,
    )
    estilo_titulo_tabela = ParagraphStyle(
        "titulo_tab", parent=styles["Heading2"],
        fontSize=13, textColor=COR_LARANJA_ESCURO,
        fontName="Helvetica-Bold", spaceAfter=3 * mm, alignment=TA_LEFT,
    )
    estilo_subtitulo_pav = ParagraphStyle(
        "subtitulo_pav", parent=styles["Normal"],
        fontSize=10, textColor=COR_LARANJA_ESCURO,
        fontName="Helvetica-Bold", spaceAfter=2 * mm, spaceBefore=5 * mm, alignment=TA_LEFT,
    )
    estilo_header = ParagraphStyle(
        "header_cell", parent=styles["Normal"],
        fontSize=8, textColor=colors.white, fontName="Helvetica-Bold",
        alignment=TA_CENTER, leading=10,
    )
    estilo_dados = ParagraphStyle(
        "dados_cell", parent=styles["Normal"],
        fontSize=8, textColor=COR_TEXTO_ESCURO, fontName="Helvetica",
        alignment=TA_CENTER, leading=10,
    )
    estilo_total = ParagraphStyle(
        "total_cell", parent=styles["Normal"],
        fontSize=8, textColor=COR_LARANJA_ESCURO, fontName="Helvetica-Bold",
        alignment=TA_CENTER, leading=10,
    )

    story = []

    # Header with logo
    logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
    linhas_cab = [f"<b>EDIFÍCIO:</b> {edificio_nome}"]

    if os.path.exists(logo_path):
        logo_img = Image(logo_path, width=40 * mm, height=40 * mm, kind="proportional")
        cabecalho_data = [[[Paragraph(l, estilo_info) for l in linhas_cab], logo_img]]
        tabela_cab = Table(cabecalho_data, colWidths=[125 * mm, 45 * mm])
        tabela_cab.setStyle(TableStyle([
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",        (1, 0), (1, 0),   "RIGHT"),
            ("RIGHTPADDING", (1, 0), (1, 0),   0),
        ]))
    else:
        tabela_cab = Table(
            [[Paragraph(l, estilo_info)] for l in linhas_cab],
            colWidths=[170 * mm],
        )

    story.append(tabela_cab)
    story.append(HRFlowable(width="100%", thickness=2, color=COR_LARANJA_ESCURO,
                             spaceAfter=6 * mm, spaceBefore=4 * mm))

    story.append(Paragraph("RESUMO DE QUANTITATIVOS", estilo_titulo_tabela))

    # 3 cols: ELEMENTO | Concreto (m³) | Formas (m²)
    col_w = [50 * mm, 60 * mm, 60 * mm]

    H = estilo_header
    D = estilo_dados
    T = estilo_total

    estilo_tabela_pav = TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),   COR_LARANJA_ESCURO),
        ("VALIGN",       (0, 0), (-1, -1),  "MIDDLE"),
        ("ALIGN",        (1, 0), (-1, -1),  "CENTER"),
        ("BACKGROUND",   (0, 2), (-1, 2),   COR_CINZA_LINHA),   # PILARES
        ("BACKGROUND",   (0, 4), (-1, 4),   COR_CINZA_LINHA),   # LAJES NERVURADAS
        ("BACKGROUND",   (0, -1), (-1, -1), COR_LARANJA_CLARO), # TOTAL
        ("GRID",         (0, 0), (-1, -1),  0.5, COR_LARANJA_MEDIO),
        ("LINEBELOW",    (0, 0), (-1, 0),   1.5, COR_LARANJA_ESCURO),
        ("TOPPADDING",   (0, 0), (-1, -1),  4),
        ("BOTTOMPADDING",(0, 0), (-1, -1),  4),
    ])

    for pav in pavimentos:
        t = _totais_quant(pav["quantitativos"])

        story.append(Paragraph(pav["nome"], estilo_subtitulo_pav))

        tabela_dados = [
            [Paragraph("ELEMENTO", H),           Paragraph("Concreto (m³)", H),                        Paragraph("Formas (m²)", H)],
            [Paragraph("VIGAS", D),              Paragraph(f"{t['vigas']['concreto']:.2f}", D),         Paragraph(f"{t['vigas']['formas']:.2f}", D)],
            [Paragraph("PILARES", D),            Paragraph(f"{t['pilares']['concreto']:.2f}", D),       Paragraph(f"{t['pilares']['formas']:.2f}", D)],
            [Paragraph("LAJES MACIÇAS", D),      Paragraph(f"{t['lajes_macicas']['concreto']:.2f}", D), Paragraph(f"{t['lajes_macicas']['formas']:.2f}", D)],
            [Paragraph("LAJES NERVURADAS", D),   Paragraph(f"{t['lajes_nervuradas']['concreto']:.2f}", D), Paragraph(f"{t['lajes_nervuradas']['formas']:.2f}", D)],
            [Paragraph("TOTAL", T),              Paragraph(f"{t['total']['concreto']:.2f}", T),         Paragraph(f"{t['total']['formas']:.2f}", T)],
        ]

        tabela = Table(tabela_dados, colWidths=col_w)
        tabela.setStyle(estilo_tabela_pav)
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

    cabecalho = extrair_cabecalho(linhas)
    quant     = extrair_quantitativos(linhas)

    edificio_nome  = cabecalho.get("titulo_geral") or cabecalho.get("edificio") or "EDIFICIO"
    pavimento_nome = cabecalho.get("pavimento") or os.path.splitext(os.path.basename(caminho_lst))[0]

    pavimentos = [{"nome": pavimento_nome, "quantitativos": quant}]

    print("Cabeçalho:")
    for k, v in cabecalho.items():
        print(f"  {k}: {v}")

    print(f"\nQuantitativos:")
    print(f"  Vigas:              {len(quant['vigas'])} elementos")
    print(f"  Pilares:            {len(quant['pilares'])} elementos")
    print(f"  Lajes Maciças:      {len(quant['lajes_macicas'])} elementos")
    print(f"  Lajes Nervuradas:   {len(quant['lajes_nervuradas'])} elementos")

    pasta_saida = os.path.join(os.path.dirname(__file__), "output")
    nome_base   = os.path.splitext(os.path.basename(caminho_lst))[0]
    print(f"\nA exportar para: {pasta_saida}/")
    exportar_dxf(pavimentos, edificio_nome, pasta_saida, nome_base)
    exportar_pdf(pavimentos, edificio_nome, pasta_saida, nome_base)
    print("\nConcluído.")


if __name__ == "__main__":
    main()
