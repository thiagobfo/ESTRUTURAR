import io
import os
import tempfile
import zipfile

from flask import Flask, jsonify, render_template, request, send_file

from extrair_lst import (
    carregar_ficheiro,
    extrair_avisos,
    extrair_cabecalho,
    extrair_lajes_nervuradas,
    extrair_quantitativos,
    exportar_dxf,
    exportar_pdf,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/processar", methods=["POST"])
def processar():
    if "ficheiro" not in request.files:
        return jsonify({"erro": "Nenhum ficheiro enviado."}), 400

    ficheiro = request.files["ficheiro"]
    if not ficheiro.filename.lower().endswith(".lst"):
        return jsonify({"erro": "O ficheiro deve ter extensão .lst"}), 400

    with tempfile.TemporaryDirectory() as tmpdir:
        lst_path = os.path.join(tmpdir, ficheiro.filename)
        ficheiro.save(lst_path)

        linhas = carregar_ficheiro(lst_path)
        dados = {
            "cabecalho":        extrair_cabecalho(linhas),
            "quantitativos":    extrair_quantitativos(linhas),
            "avisos":           extrair_avisos(linhas),
            "lajes_nervuradas": extrair_lajes_nervuradas(linhas),
        }

        nome_base  = os.path.splitext(ficheiro.filename)[0]
        output_dir = os.path.join(tmpdir, "output")

        exportar_pdf(dados, output_dir, nome_base)
        exportar_dxf(dados, output_dir, nome_base)

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
