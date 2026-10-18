# PocketBase mit eingebauten Migrationen.
# Portainer klont das Repo in sein eigenes Volume – ein relativer Bind-Mount
# auf ./database/pb_migrations zeigt auf dem Host ins Leere. Deshalb werden
# die Migrationen ins Image kopiert und beim Start von PocketBase angewendet.
FROM ghcr.io/muchobien/pocketbase:0.40.5

COPY pb_migrations /pb_migrations
