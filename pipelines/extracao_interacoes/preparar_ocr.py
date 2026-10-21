"""Baixa somente o modelo oficial portugues; nunca envia documentos."""
import hashlib
from pathlib import Path
from urllib.request import urlopen

URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/por.traineddata"
SHA256 = "c4932b937207a9514b7514d518b931a99938c02a28a5a5a553f8599ed58b7deb"


def main():
    destino = Path(__file__).resolve().parent / "modelos_ocr/tessdata/por.traineddata"
    if destino.is_file():
        if hashlib.sha256(destino.read_bytes()).hexdigest() == SHA256:
            print("Modelo OCR em portugues ja instalado.")
            return
        raise RuntimeError("Ja existe outro modelo nesse caminho; nao sera sobrescrito.")
    with urlopen(URL, timeout=60) as resposta:
        dados = resposta.read(10_000_001)
    if hashlib.sha256(dados).hexdigest() != SHA256:
        raise RuntimeError("Checksum inesperado no modelo OCR; download descartado.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("xb") as arquivo:
        arquivo.write(dados)
    print(f"OCR em portugues preparado: {destino}")


if __name__ == "__main__":
    main()
