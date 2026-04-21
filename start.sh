#!/bin/bash
cd /home/pps-nipa/NIQ/fish/side/g/US_Stock_searcher
/home/pps-nipa/anaconda3/bin/gunicorn \
  --bind 0.0.0.0:5065 \
  --workers 2 \
  --threads 4 \
  --timeout 300 \
  --worker-class gthread \
  app:app
