"""Number and time formatting for the user (Slovak: decimal comma)."""


def num(v, dec):
    return ('%.*f' % (dec, v)).replace('.', ',')


def hms(t):
    t = int(round(t))
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return '%d:%02d:%02d' % (h, m, s) if h else '%d:%02d' % (m, s)
