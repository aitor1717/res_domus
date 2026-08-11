FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# UID 1000 matches the default first-user UID on most Linux desktops, so
# writes to the bind-mounted ./data volume need no permission fix. Different
# host UID: set APP_UID=$(id -u) in .env (docker-compose.yml passes it
# through as a build arg).
ARG APP_UID=1000
RUN useradd -m -u ${APP_UID} appuser
USER appuser

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "120", "app:create_app()"]
