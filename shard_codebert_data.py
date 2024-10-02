import pickle, transformers, tqdm, os
import numpy as np

code, id, comment = pickle.load(open("/dataset/codebert/extracted_pair_raw.pkl", "rb"))
concated = [" ".join(item) for item in zip(id, code)]
num_codes = len(concated)
np.random.seed(12345)
shuffled_idx = np.random.choice(num_codes, num_codes, False)
num_blocks = 4096
block_size = (num_codes // num_blocks) + 1
output_dir = "/dataset/codebert/source"
linedelimiter = "\r\n"

for i in range(num_blocks):
    with open(os.path.join(output_dir, f"block_{i}.txt"), "w") as f:
        for idx in shuffled_idx[i * block_size : (i + 1) * block_size]:
            line = concated[idx]
            if "\r\n" in line:
                line = line.replace("\r\n", "\n")
            f.write(f"{line}{linedelimiter}")
    print(f"Block {i} finished")
