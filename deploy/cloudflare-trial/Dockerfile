# Local base was inspected as linux/amd64, sha256:000cce72e40af1ee75a3f188b80a5ab6716490bd2d2f3bbf015ee73340635887.
# Experimental build only. No production credentials or volumes are copied.
FROM parserium-collector:hosted-local
USER root
RUN mkdir -p /trial/tessdata
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/e12c65a915945e4c28e237a9b52bc4a8f39a0cec/eng.traineddata', '/trial/tessdata/eng.traineddata')"
COPY deploy/cloudflare-trial/measure.py deploy/cloudflare-trial/server.py /trial/
COPY tests/fixtures/analysis/ruled-table.pdf /trial/ruled-table.pdf
USER 10001:10001
ENTRYPOINT ["python", "/trial/server.py"]
CMD []
