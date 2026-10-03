def level_one():
    level_two()


def level_two():
    level_three()


def level_three():
    numbers = [1, 2, 3]
    return numbers[10]


level_one()