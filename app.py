import io
import os
import re
import tempfile
import zipfile

from flask import Flask, jsonify, render_template, request, send_file

from extrair_lst import (
    carregar_ficheiro,
    extrair_cabecalho,
    extrair_quantitativos,
    exportar_dxf,
    exportar_pdf,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/processar", methods=["POST"])
def processar():
    ficheiros = request.files.getlist("ficheiro")
    if not ficheiros or all(f.filename == "" for f in ficheiros):
        return jsonify({"erro": "Nenhum ficheiro enviado."}), 400

    for f in ficheiros:
        if not f.filename.lower().endswith(".lst"):
            return jsonify({"erro": f"Ficheiro inválido: {f.filename}. Apenas .lst"}), 400

    edificio_nome = request.form.get("edificio", "").strip()

    with tempfile.TemporaryDirectory() as tmpdir:
        pavimentos = []
        for ficheiro in ficheiros:
            lst_path = os.path.join(tmpdir, ficheiro.filename)
            ficheiro.save(lst_path)
            linhas = carregar_ficheiro(lst_path)

            if not edificio_nome:
                cab = extrair_cabecalho(linhas)
                edificio_nome = cab.get("titulo_geral") or cab.get("edificio") or "EDIFICIO"

            nome_pav = os.path.splitext(ficheiro.filename)[0]
            pavimentos.append({
                "nome": nome_pav,
                "quantitativos": extrair_quantitativos(linhas),
            })

        if not edificio_nome:
            edificio_nome = "EDIFICIO"

        pavimentos.sort(key=lambda p: [int(c) if c.isdigit() else c.lower()
                                        for c in re.split(r"(\d+)", p["nome"])])

        nome_base = re.sub(r'[<>:"/\\|?*]', "_", edificio_nome)
        output_dir = os.path.join(tmpdir, "output")

        exportar_pdf(pavimentos, edificio_nome, output_dir, nome_base)
        exportar_dxf(pavimentos, edificio_nome, output_dir, nome_base)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(os.path.join(output_dir, f"{nome_base}.pdf"), f"{nome_base}.pdf")
            zf.write(os.path.join(output_dir, f"{nome_base}.dxf"), f"{nome_base}.dxf")

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{nome_base}.zip",
    )


if __name__ == "__main__":
    app.run(debug=True)
