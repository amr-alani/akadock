r"""Self-check for covalent warhead detection. Run:  python\python.exe -I scripts\selfcheck_warheads.py

The group that reacts must be found, and groups that only look similar must not be: nirmatrelvir
carries an unreactive CF3-amide cap next to its real nitrile warhead, and an ordinary amide is not
a warhead at all. Needs no internet.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from rdkit import Chem  # noqa: E402

import pipeline as P  # noqa: E402

CASES = [
    ("nirmatrelvir (nitrile warhead, CF3-amide cap)", "nitrile",
     "CC1(C)[C@@H]2[C@H]1[C@H](C(=O)N[C@@H](C[C@@H]1CCNC1=O)C#N)N(C2)C(=O)[C@@H](NC(=O)C(F)(F)F)C(C)(C)C"),
    ("afatinib (substituted acrylamide)", "acrylamide",
     "CN(C)C/C=C/C(=O)Nc1cc2c(Nc3ccc(F)c(Cl)c3)ncnc2cc1O[C@H]1CCOC1"),
    ("ibrutinib (acrylamide)", "acrylamide",
     "C=CC(=O)N1CCC[C@@H](C1)n1nc(-c2ccc(Oc3ccccc3)cc2)c2c(N)ncnc21"),
    ("penicillin G (beta-lactam)", "beta-lactam",
     "CC1(C)S[C@@H]2[C@H](NC(=O)Cc3ccccc3)C(=O)N2[C@H]1C(=O)O"),
    ("trifluoroacetamide cap alone", None, "FC(F)(F)C(=O)NCC"),
    ("ordinary amide", None, "CC(=O)Nc1ccccc1"),
    ("ethanol", None, "CCO"),
]

bad = [n for n, s, _ in P.WARHEADS if Chem.MolFromSmarts(s) is None]
assert not bad, f"these warhead patterns are not valid SMARTS: {bad}"

fails = 0
for name, want, smiles in CASES:
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None, f"bad test SMILES for {name}"
    hit = P.find_warhead(mol)
    got = hit[0] if hit else None
    ok = got == want
    fails += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name:44s} expected {str(want):12s} got {got}")
    if ok and hit:
        atom = mol.GetAtomWithIdx(hit[1])
        print(f"       attacking atom: {atom.GetSymbol()}{hit[1]}")

assert not fails, f"{fails} warhead case(s) wrong"
print("\nwarhead detection: all cases as expected")
