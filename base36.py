def to_base36(num : int):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if num == 0:
        return "0"
    
    base36 = ""
    while num:
        num, remainder = divmod(num, 36)
        base36 = digits[remainder] + base36
    
    return base36

def from_base36(string : str):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    string = string.lower()
    base10 = 0
    
    for char in string:
        base10 = base10 * 36 + digits.index(char)
    
    return base10