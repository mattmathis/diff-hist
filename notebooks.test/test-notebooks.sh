#!/bin/bash

# This test script assembles a provisional release and launches a notebook

cp ../notebooks.stage/* .
cp ../notebooks/index.ipynb .

jupyter notebook . &
