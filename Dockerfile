FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]==3.2.*" google-auth requests "flask==3.1.*" cryptography "gunicorn==23.*"
COPY collectors ./collectors
COPY seo ./seo
COPY webapp ./webapp
COPY db ./db
COPY scripts ./scripts
COPY seeds ./seeds
COPY start_local.py ./
ENV PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080
CMD gunicorn --chdir webapp --workers 2 --timeout 120 --bind 0.0.0.0:${PORT} app:app
