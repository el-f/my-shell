# Debian 12 minimal -- wget only, no curl (Gimli: Minimalist Server Admin)
FROM debian:12-slim
RUN apt-get update && apt-get install -y wget git python3 python3-venv && rm -rf /var/lib/apt/lists/*
# Explicitly NO curl
COPY . /opt/my-shell-repo
RUN find /opt/my-shell-repo -type f \( -name '*.sh' -o -name '*.py' -o -name '*.toml' -o -name '*.nu' -o -name '*.xsh' -o -name '*.template' \) -exec sed -i 's/\r$//' {} +
RUN cd /opt/my-shell-repo && git init && git checkout -b main && git add -A \
    && git -c user.name=ci -c user.email=ci@test commit -m init
CMD ["sleep", "infinity"]
