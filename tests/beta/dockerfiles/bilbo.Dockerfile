# Ubuntu 22.04 (Bilbo: Data Scientist, xonsh focus)
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y curl git python3 python3-pip python3-venv && rm -rf /var/lib/apt/lists/*
COPY . /opt/my-shell-repo
RUN find /opt/my-shell-repo -type f \( -name '*.sh' -o -name '*.py' -o -name '*.toml' -o -name '*.nu' -o -name '*.xsh' -o -name '*.template' \) -exec sed -i 's/\r$//' {} +
RUN cd /opt/my-shell-repo && git init && git checkout -b main && git add -A \
    && git -c user.name=ci -c user.email=ci@test commit -m init
CMD ["sleep", "infinity"]
