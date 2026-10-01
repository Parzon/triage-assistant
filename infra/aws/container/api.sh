# The api reads one DATABASE_URL; ECS injects the secret as separate values, so compose
# it here. The generated passwords have no punctuation, so they need no URL-encoding.
export DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
exec gunicorn app.asgi:app -c gunicorn.conf.py
