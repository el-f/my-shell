# openSUSE Tumbleweed (Pippin: Power User)
FROM opensuse/tumbleweed:latest
RUN zypper --non-interactive install curl git python311 python311-pip tar gzip findutils && zypper clean -a
RUN ln -sf /usr/bin/python3.11 /usr/bin/python3
COPY . /opt/my-shell-repo
RUN find /opt/my-shell-repo -type f \( -name '*.sh' -o -name '*.py' -o -name '*.toml' -o -name '*.nu' -o -name '*.xsh' -o -name '*.template' \) -exec sed -i 's/\r$//' {} +
RUN cd /opt/my-shell-repo && git init && git checkout -b main && git add -A \
    && git -c user.name=ci -c user.email=ci@test commit -m init
CMD ["sleep", "infinity"]
