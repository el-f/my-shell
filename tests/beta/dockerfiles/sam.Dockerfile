# Arch Linux with full dev tools (Sam: DevOps Engineer)
FROM archlinux:latest
RUN pacman -Syu --noconfirm && pacman -S --noconfirm curl git python base-devel rustup docker kubectl
RUN rustup default stable
COPY . /opt/my-shell-repo
RUN find /opt/my-shell-repo -type f \( -name '*.sh' -o -name '*.py' -o -name '*.toml' -o -name '*.nu' -o -name '*.xsh' -o -name '*.template' \) -exec sed -i 's/\r$//' {} +
RUN cd /opt/my-shell-repo && git init && git checkout -b main && git add -A \
    && git -c user.name=ci -c user.email=ci@test commit -m init
CMD ["sleep", "infinity"]
