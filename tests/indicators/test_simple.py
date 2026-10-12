import sys
print("sys.path[0:3]:", sys.path[:3])
import indicators
print("indicators OK:", indicators.__file__)
from indicators.ma import MA
print("MA OK:", MA)
