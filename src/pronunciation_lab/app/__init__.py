"""Personal Pronunciation Lab — local web application (M3).

The app is a thin, honest front end over the M1 evidence layer: it takes a
recording and a target text, runs a real engine through `safe_analyze`, and
presents the evidence per word and per sound, with exact playback of the
analysed audio. It never invents a score and never turns recogniser output
into a pronunciation verdict.
"""
