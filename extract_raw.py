import pickle

code = []
comment = []
id = []
num_code = {}
for lang in ["python", "java", "javascript", "go", "php", "ruby"]:
    print(f"Extracting {lang}")
    num_bimodal = 0
    num_uni = 0
    num_code[lang] = 0
    lang_corpus = pickle.load(open(f"/dataset/codebert/{lang}_dedupe_definitions_v2.pkl", "rb"))
    for i, instance in enumerate(lang_corpus):
        code.append(instance["function"])
        id.append(f"{lang}_{i}")
        comment.append(instance["docstring"])
        num_code[lang] += 1
        if not instance["docstring"]:
            num_uni += 1
        else:
            num_bimodal += 1
    print(f"{lang}, bimodal data: {num_bimodal}, unimodal data: {num_uni}")