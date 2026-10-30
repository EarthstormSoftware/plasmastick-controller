# The firmware: main.py imports this once otaboot has dealt with any update.
# App Base (base/) runs the product (product.py).
import product
from base.device import run

run(product)
