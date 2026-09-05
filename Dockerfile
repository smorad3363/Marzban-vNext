ARG PYTHON_VERSION=3.12

FROM mysql:26.7.0 AS mysql-client

FROM python:$PYTHON_VERSION-slim AS build

ENV PYTHONUNBUFFERED=1

WORKDIR /code

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl unzip gcc python3-dev \
    && curl -L https://github.com/Gozargah/Marzban-scripts/raw/master/install_latest_xray.sh | bash \
    && rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt /code/
RUN python3 -m pip install --upgrade pip \
    && pip install --no-cache-dir --upgrade -r /code/requirements.txt

FROM python:$PYTHON_VERSION-slim

ENV PYTHON_LIB_PATH=/usr/local/lib/python${PYTHON_VERSION%.*}/site-packages
WORKDIR /code

RUN apt-get update \
    && apt-get install -y --no-install-recommends libncurses6 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* $PYTHON_LIB_PATH/*

COPY --from=build $PYTHON_LIB_PATH $PYTHON_LIB_PATH
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=mysql-client /usr/bin/mysql /usr/bin/mysqldump /usr/local/bin/
COPY --from=build /usr/local/share/xray /usr/local/share/xray

COPY . /code

RUN sed -i 's/\r$//' /code/marzban-cli.py \
    && mysql --version && mysqldump --version \
    && ln -s /code/marzban-cli.py /usr/bin/marzban-cli \
    && chmod +x /usr/bin/marzban-cli \
    && SQLALCHEMY_DATABASE_URL=mysql+pymysql://marzban:marzban@127.0.0.1:3306/marzban \
       python3 /code/marzban-cli.py completion install --shell bash

CMD ["bash", "-c", "alembic upgrade head && exec python main.py"]
