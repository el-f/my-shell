# ARM64 Debian for Raspberry Pi testing (Legolas: ARM Tinkerer)
# Run with: docker run --platform linux/arm64 (requires QEMU)
FROM debian:12
RUN apt-get update && apt-get install -y curl git python3 python3-venv && rm -rf /var/lib/apt/lists/*
COPY . /opt/my-shell-repo
RUN find /opt/my-shell-repo -type f \( -name '*.sh' -o -name '*.py' -o -name '*.toml' -o -name '*.nu' -o -name '*.xsh' -o -name '*.template' \) -exec sed -i 's/\r$//' {} +
RUN cd /opt/my-shell-repo && git init && git checkout -b main && git add -A \
    && git -c user.name=ci -c user.email=ci@test commit -m init
CMD ["sleep", "infinity"]
