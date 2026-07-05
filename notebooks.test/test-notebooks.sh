#!/bin/bash

# This test script assembles a provisional release and launches a notebook

pkill jupyter-noteboo
mkdir -p internal
cp ../notebooks.stage/*.ipynb .
cp ../notebooks.stage/internal/*.ipynb internal/
cp ../notebooks/index.ipynb .
cp ../notebooks/internal/index.ipynb internal/

jupyter notebook . &
