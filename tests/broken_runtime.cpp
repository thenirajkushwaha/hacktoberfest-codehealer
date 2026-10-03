#include <iostream>

int main() {
    int a = 10;
    int b = 0;

    if (b != 0) {
        std::cout << a / b << std::endl;
    } else {
        // Handle division by zero to prevent runtime exception and ensure exit code 0
    }

    return 0;
}
