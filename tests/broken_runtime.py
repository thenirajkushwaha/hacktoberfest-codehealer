"""
Sample broken script with runtime exceptions (IndexError and ZeroDivisionError).
Used to demonstrate heal's autonomous runtime crash interception and repair.
"""

def process_metrics(readings):
    print("Processing system sensor metrics...")
    
    # Bug 1: IndexError due to off-by-one loop boundary
    clean_readings = []
    for i in range(len(readings) + 1):
        clean_readings.append(readings[i] * 1.5)
        
    # Bug 2: ZeroDivisionError
    divisor = len(readings) - len(clean_readings)
    normalized_score = sum(clean_readings) / divisor
    
    return normalized_score


if __name__ == "__main__":
    sensor_stream = [12.4, 15.8, 14.2, 19.0, 22.1]
    score = process_metrics(sensor_stream)
    print(f"Metrics processed successfully. Normalized score: {score:.2f}")
