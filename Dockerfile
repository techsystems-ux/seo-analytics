FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]==3.2.*" google-auth requests "flask==3.1.*" cryptography "gunicorn==23.*" "python-pptx==1.0.*"
COPY collectors ./collectors
COPY seo ./seo
COPY webapp ./webapp
COPY db ./db
COPY scripts ./scripts
COPY seeds ./seeds
COPY start_local.py ./
ENV PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080
# Migrations run at start (Render's free plan has no pre-deploy step). One worker, so the server can see the pulls it started.
CMD python scripts/migrate.py && exec gunicorn --chdir webapp --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:${PORT} app:app
