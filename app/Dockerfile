FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# UID 1000 matches the default first-user UID on most single-user Linux
# desktops (including the host this was built/tested against), so writes to
# the bind-mounted ./data volume work out of the box for the common case. If
# your host user has a different UID, set APP_UID=$(id -u) in your .env file
# (docker-compose.yml passes it through as a build arg automatically).
ARG APP_UID=1000
RUN useradd -m -u ${APP_UID} appuser
USER appuser

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "120", "app:create_app()"]
