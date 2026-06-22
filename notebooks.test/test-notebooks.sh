#!/bin/bash

# This test script assembles a provisional release and launches a notebook

mkdir -p internal
cp ../notebooks.stage/*.ipynb .
cp ../notebooks.stage/internal/*.ipynb internal/
cp ../notebooks/index.ipynb .

jupyter notebook . &
