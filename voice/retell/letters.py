"""Letter questions answered by the bridge itself ("how many e's in seventeen?"): the model passes
the word and the letter, and gets the count and the spelling back. Models miscount letters (the
native agent said three e's in seventeen on its gate call), and Retell's Code tool can't take
arguments, so this is a plain function on the bridge (voice/retell_native, next copy)."""


def count_letters(word: str, letter: str = "") -> dict:
    letters = [c for c in (word or "").lower() if c.isalpha()]
    if not letters:
        return {"ok": False, "say": "", "data": {}, "refused": "no_word"}
    data = {"word": word, "spelled": "".join(letters)}
    wanted = next((c for c in (letter or "").lower() if c.isalpha()), "")
    if wanted:
        data.update(letter=wanted, count=letters.count(wanted))
    return {"ok": True, "say": "", "data": data}
