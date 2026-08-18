web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60 --access-logfile - --error-logfile -
release: python -c "import app; app.app.app_context().push(); app.bootstrap()"
