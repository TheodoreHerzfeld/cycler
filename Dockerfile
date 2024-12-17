FROM python:3-alpine

RUN apk add skopeo curl
RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

WORKDIR /usr/src/app

COPY ./requirements.txt ./
RUN pip install -r requirements.txt --break-system-packages

COPY ./main.py ./main.py

CMD [ "/usr/local/bin/python", "./main.py" ]