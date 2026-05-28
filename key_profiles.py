# Each key maps to its diatonic chords (I ii iii IV V vi vii°)
KEY_PROFILES = {
    # Major keys
    "C":  ["C", "Dm", "Em", "F", "G", "Am", "Bdim"],
    "C#": ["C#", "D#m", "Fm", "F#", "G#", "A#m", "Cdim"],
    "D":  ["D", "Em", "F#m", "G", "A", "Bm", "C#dim"],
    "D#": ["D#", "Fm", "Gm", "G#", "A#", "Cm", "Ddim"],
    "E":  ["E", "F#m", "G#m", "A", "B", "C#m", "D#dim"],
    "F":  ["F", "Gm", "Am", "Bb", "C", "Dm", "Edim"],
    "F#": ["F#", "G#m", "A#m", "B", "C#", "D#m", "Fdim"],
    "G":  ["G", "Am", "Bm", "C", "D", "Em", "F#dim"],
    "G#": ["G#", "A#m", "Cm", "C#", "D#", "Fm", "Gdim"],
    "A":  ["A", "Bm", "C#m", "D", "E", "F#m", "G#dim"],
    "A#": ["A#", "Cm", "Dm", "D#", "F", "Gm", "Adim"],
    "B":  ["B", "C#m", "D#m", "E", "F#", "G#m", "A#dim"],

    # Minor keys (natural minor: i ii° III iv v VI VII)
    "Cm":  ["Cm", "Ddim", "D#", "Fm", "Gm", "G#", "A#"],
    "C#m": ["C#m", "D#dim", "E", "F#m", "G#m", "A", "B"],
    "Dm":  ["Dm", "Edim", "F", "Gm", "Am", "A#", "C"],
    "D#m": ["D#m", "Fdim", "F#", "G#m", "A#m", "B", "C#"],
    "Em":  ["Em", "F#dim", "G", "Am", "Bm", "C", "D"],
    "Fm":  ["Fm", "Gdim", "G#", "A#m", "Cm", "C#", "D#"],
    "F#m": ["F#m", "G#dim", "A", "Bm", "C#m", "D", "E"],
    "Gm":  ["Gm", "Adim", "A#", "Cm", "Dm", "D#", "F"],
    "G#m": ["G#m", "A#dim", "B", "C#m", "D#m", "E", "F#"],
    "Am":  ["Am", "Bdim", "C", "Dm", "Em", "F", "G"],
    "A#m": ["A#m", "Cdim", "C#", "D#m", "Fm", "F#", "G#"],
    "Bm":  ["Bm", "C#dim", "D", "Em", "F#m", "G", "A"],

    # Enharmonic aliases
    "Db":  ["C#", "D#m", "Fm", "F#", "G#", "A#m", "Cdim"],   # = C#
    "Eb":  ["D#", "Fm", "Gm", "G#", "A#", "Cm", "Ddim"],     # = D#
    "Gb":  ["F#", "G#m", "A#m", "B", "C#", "D#m", "Fdim"],   # = F#
    "Ab":  ["G#", "A#m", "Cm", "C#", "D#", "Fm", "Gdim"],    # = G#
    "Bb":  ["A#", "Cm", "Dm", "D#", "F", "Gm", "Adim"],      # = A#
    "Ebm": ["D#m", "Fdim", "F#", "G#m", "A#m", "B", "C#"],   # = D#m
    "Abm": ["G#m", "A#dim", "B", "C#m", "D#m", "E", "F#"],   # = G#m
    "Bbm": ["A#m", "Cdim", "C#", "D#m", "Fm", "F#", "G#"],   # = A#m
    "Dbm": ["C#m", "D#dim", "E", "F#m", "G#m", "A", "B"],    # = C#m
    "Gbm": ["F#m", "G#dim", "A", "Bm", "C#m", "D", "E"],     # = F#m
}

# Bonus: common borrowed chords per key (adds tolerance for songs that borrow chords)
BORROWED_CHORDS = {
    "C":   ["Fm", "Bb", "Ab", "Eb"],
    "G":   ["Cm", "F", "Eb", "Bb"],
    "D":   ["Gm", "C", "Bb", "F"],
    "A":   ["Dm", "G", "F", "C"],
    "E":   ["Am", "D", "C", "G"],
    "F#m": ["A", "E", "B", "C#"],
    "Am":  ["E", "A", "D", "G"],
    "Em":  ["B", "E", "A", "D"],
    "Bm":  ["F#", "B", "E", "A"],
    "F#":  ["C#m", "G#m", "A", "B"],
    "Ab":  ["Fm", "Bbm", "Eb", "Db"],
    "Eb":  ["Cm", "Fm", "Bb", "Ab"],
    "Bb":  ["Gm", "Cm", "F", "Eb"],
    "F":   ["Dm", "Gm", "C", "Bb"],
}