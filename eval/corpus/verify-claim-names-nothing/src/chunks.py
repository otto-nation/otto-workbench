def chunks(seq, size):
    return [seq[i:i + size] for i in range(0, len(seq) - size + 1, size)]
