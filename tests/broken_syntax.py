"""
Sample broken script with syntax and indentation errors.
Used to demonstrate heal's autonomous syntax error interception and repair.
"""

def calculate_stats(numbers):
    total = 0
    for num in numbers
        total += num
    return total / len(numbers)

if __name__ == "__main__":
    data = [10, 20, 30, 40, 50]
    result = calculate_stats(data)
    print(f"Calculated average: {result}")
