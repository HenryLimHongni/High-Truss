#include "utils/current_time.h"

double get_current_time() {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    long long usec = tv.tv_sec * 1000000 + tv.tv_usec;
    return usec / 1000000.0;
}