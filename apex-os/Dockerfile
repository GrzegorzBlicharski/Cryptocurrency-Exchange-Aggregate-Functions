FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APEX_DATA_DIR=/data APEX_DATABASE_URL=sqlite:////data/apex.db
WORKDIR /app
COPY pyproject.toml README.md ./
COPY apex ./apex
RUN pip install --no-cache-dir . && useradd -r -u 10001 apex && mkdir /data && chown apex /data
USER apex
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/login')" || exit 1
CMD ["python", "-m", "apex", "serve", "--host", "0.0.0.0", "--port", "8000"]
