# README

This repository contains four programs:

- `4-framework`
- `4-convert`
- `DET-Five`
- `DDT-Five`



```bash
# 4-framework
cd 4-framework
mkdir -p build
cd build
g++ -O3 -std=c++17 -I.. -I../include $(find ../src -name "*.cc") -o my_program
./my_program file-path

# 4-convert
cd 4-convert
mkdir -p build
cd build
g++ -O3 -std=c++17 -I.. -I../include $(find ../src -name "*.cc") -o my_program
./my_program file-path

# DET-Five
cd DET-Five
g++ -std=gnu++17 -O3 -march=native main.cpp -o fivecycle
./fivecycle filepath

# DDT-Five
cd DDT-Five
g++ -std=gnu++17 -O3 -march=native main.cpp -o fivecycle
./fivecycle filepath
