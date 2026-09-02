# HireB - production container for Azure App Service.
# Bakes in the Microsoft ODBC Driver 18 so mssql-django can reach Azure SQL.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_DEBUG=False

# --- System deps + Microsoft ODBC Driver 18 (Debian 12 / bookworm) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates apt-transport-https unixodbc-dev gcc g++ \
 && curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
 && apt-get purge -y curl gnupg \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect static into STATIC_ROOT (served by WhiteNoise at runtime)
RUN python manage.py collectstatic --noinput

# Azure App Service sends traffic to $PORT (default 8000 here)
ENV PORT=8000
EXPOSE 8000

# Apply DB migrations on boot, then serve with gunicorn
CMD python manage.py migrate --noinput && \
    gunicorn HR_management.wsgi:application --bind 0.0.0.0:${PORT} --workers 3 --timeout 120
