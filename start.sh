#!/bin/bash
# Script de inicialização para VPS Linux
set -e

echo "==> Instalando dependências..."
pip install -r requirements.txt

echo "==> Aplicando migrations..."
python manage.py migrate --noinput

echo "==> Coletando arquivos estáticos..."
python manage.py collectstatic --noinput

echo "==> Iniciando servidor de produção..."
exec gunicorn mapa_project.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
