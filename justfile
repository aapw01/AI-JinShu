set shell := ["bash", "-cu"]

default:
    @just --list

install:
    make install

dev:
    make dev

stop:
    make stop

dev-reset:
    make dev-reset

logs:
    make logs

test:
    make test

lint:
    make lint
