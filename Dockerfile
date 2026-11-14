FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /engine
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home agent
COPY --chown=agent:agent app app
COPY --chown=agent:agent migrations migrations
COPY --chown=agent:agent scripts scripts
COPY --chown=agent:agent alembic.ini .
USER agent
EXPOSE 8000
CMD ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--no-proxy-headers","--no-access-log"]
