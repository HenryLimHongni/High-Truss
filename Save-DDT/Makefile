CXX ?= g++
CXXFLAGS ?= -O3 -march=native -DNDEBUG -std=c++17 -Wall -Wextra
LDFLAGS ?=

TARGET := five_cycle_decomp
SRC := main.cpp

all: $(TARGET)

$(TARGET): $(SRC) *.h
	$(CXX) $(CXXFLAGS) $(SRC) -o $(TARGET) $(LDFLAGS)

clean:
	rm -f $(TARGET)
