"""Five complete palettes, with independent light/dark appearance preferences."""

PALETTE_LABELS = {"lavender": "鸢尾紫", "ocean": "海洋蓝", "forest": "森林绿",
                  "sepia": "暖纸棕", "graphite": "石墨灰"}
PALETTE_CHOICES = {label: name for name, label in PALETTE_LABELS.items()}

PALETTES = {
    "lavender": {
        "BG": ("#F9F7FD", "#1C1928"), "PANEL": ("#FFFFFF", "#272232"),
        "INK": ("#332B49", "#F2EDF9"), "MUTED": ("#6D607E", "#BEB2D0"),
        "ACCENT": ("#6D46B8", "#C0A9FF"), "HOVER": ("#6543B1", "#D5C1FF"),
        "BORDER": ("#DDD2EF", "#4A3D61"), "SIDEBAR": ("#F0EAF9", "#241F33"),
        "SELECTED": ("#E5D9F7", "#403153"), "FIELD": ("#FBF9FE", "#211D2D"),
        "CONTROL": ("#F1EBFA", "#352C46"), "CONTROL_BUTTON": ("#E6DDF5", "#49395F"),
        "CONTROL_HOVER": ("#DACBED", "#5B4775"), "QUIET_HOVER": ("#E8DDF6", "#3C304E"),
        "SECONDARY_HOVER": ("#F3ECFC", "#3B304B"), "DISABLED": ("#9A8FAA", "#9386A7"),
        "TRACK": ("#DDD3EC", "#4A3D60"), "SCROLLBAR": ("#DACFEA", "#514262"),
        "SCROLLBAR_HOVER": ("#C3AEDF", "#6C5782"), "PRIMARY_FILL": ("#7754C3", "#805EBB"),
        "PRIMARY_HOVER": ("#6543B1", "#845FC1"), "SUCCESS": ("#327762", "#8BD0B4"),
    },
    "ocean": {
        "BG": ("#F5F9FD", "#151F2B"), "PANEL": ("#FFFFFF", "#202D3C"),
        "INK": ("#233D54", "#E9F2FA"), "MUTED": ("#596D80", "#AFC1D3"),
        "ACCENT": ("#28689C", "#8DC5F4"), "HOVER": ("#1F5686", "#ADD8FC"),
        "BORDER": ("#CEDFEB", "#3A5168"), "SIDEBAR": ("#E8F1F8", "#1B2837"),
        "SELECTED": ("#D7E8F6", "#2A4862"), "FIELD": ("#FAFCFF", "#192533"),
        "CONTROL": ("#EAF3FA", "#293B4F"), "CONTROL_BUTTON": ("#D9E8F5", "#35506B"),
        "CONTROL_HOVER": ("#C5DAEC", "#456885"), "QUIET_HOVER": ("#DCEBF7", "#2B4259"),
        "SECONDARY_HOVER": ("#F0F6FC", "#2C4056"), "DISABLED": ("#8A9AAA", "#7D93AB"),
        "TRACK": ("#D3E2ED", "#3C546E"), "SCROLLBAR": ("#C6D7E6", "#44617E"),
        "SCROLLBAR_HOVER": ("#A9C3D8", "#5A7B9B"), "PRIMARY_FILL": ("#28689C", "#306A9B"),
        "PRIMARY_HOVER": ("#1F5686", "#3375AB"), "SUCCESS": ("#327762", "#8BD0B4"),
    },
    "forest": {
        "BG": ("#F6FAF7", "#18221D"), "PANEL": ("#FFFFFF", "#25332B"),
        "INK": ("#263E30", "#EBF4ED"), "MUTED": ("#5B7163", "#B3C6B9"),
        "ACCENT": ("#2F7453", "#9FD5B2"), "HOVER": ("#255F44", "#BBE7CA"),
        "BORDER": ("#D2E4D7", "#405849"), "SIDEBAR": ("#EBF3EC", "#1E2B23"),
        "SELECTED": ("#DCEEDF", "#34533E"), "FIELD": ("#FBFDFB", "#1D2A22"),
        "CONTROL": ("#ECF5EE", "#2C4133"), "CONTROL_BUTTON": ("#DDECE0", "#3C5845"),
        "CONTROL_HOVER": ("#C8DFCE", "#4E7059"), "QUIET_HOVER": ("#E0EFE4", "#304B39"),
        "SECONDARY_HOVER": ("#F1F8F3", "#2E4636"), "DISABLED": ("#8A9D90", "#82998B"),
        "TRACK": ("#D8E7DD", "#45604D"), "SCROLLBAR": ("#CADFCF", "#496852"),
        "SCROLLBAR_HOVER": ("#AECDB6", "#62896D"), "PRIMARY_FILL": ("#337654", "#367252"),
        "PRIMARY_HOVER": ("#255F44", "#3C7B59"), "SUCCESS": ("#327762", "#8BD0B4"),
    },
    "sepia": {
        "BG": ("#FBF8F0", "#252119"), "PANEL": ("#FFFEFA", "#342E23"),
        "INK": ("#493B2C", "#F5EDDE"), "MUTED": ("#73624D", "#C8B89E"),
        "ACCENT": ("#7E5633", "#DABA8F"), "HOVER": ("#6F4D2D", "#EDD1AC"),
        "BORDER": ("#E6DAC4", "#5B4D37"), "SIDEBAR": ("#F1EADA", "#2D271D"),
        "SELECTED": ("#EADBC1", "#59472E"), "FIELD": ("#FFFCF5", "#2B251B"),
        "CONTROL": ("#F5EDDD", "#433829"), "CONTROL_BUTTON": ("#EADDC6", "#5D4A30"),
        "CONTROL_HOVER": ("#DCC7A8", "#765E3D"), "QUIET_HOVER": ("#EDE1CB", "#4C3D29"),
        "SECONDARY_HOVER": ("#FAF3E5", "#483B28"), "DISABLED": ("#A3947D", "#A18D70"),
        "TRACK": ("#E3D7C1", "#635137"), "SCROLLBAR": ("#D9CAB1", "#6B583C"),
        "SCROLLBAR_HOVER": ("#BFA98B", "#8A7250"), "PRIMARY_FILL": ("#87603B", "#906F44"),
        "PRIMARY_HOVER": ("#6F4D2D", "#946C3D"), "SUCCESS": ("#4D7253", "#A6C99A"),
    },
    "graphite": {
        "BG": ("#F6F7F9", "#191C21"), "PANEL": ("#FFFFFF", "#252930"),
        "INK": ("#30353D", "#ECEFF4"), "MUTED": ("#626B78", "#B5BFCD"),
        "ACCENT": ("#52657D", "#B9C9DF"), "HOVER": ("#3F5269", "#D3DEF0"),
        "BORDER": ("#D8DEE7", "#424B5A"), "SIDEBAR": ("#EBEEF3", "#20242C"),
        "SELECTED": ("#DCE3EC", "#38465B"), "FIELD": ("#FAFBFD", "#1E2229"),
        "CONTROL": ("#EEF1F6", "#303744"), "CONTROL_BUTTON": ("#DFE5EE", "#434F64"),
        "CONTROL_HOVER": ("#CED7E3", "#56657D"), "QUIET_HOVER": ("#E1E7F0", "#354154"),
        "SECONDARY_HOVER": ("#F1F4F8", "#333C4B"), "DISABLED": ("#929BAA", "#8995A8"),
        "TRACK": ("#D8DFE9", "#465269"), "SCROLLBAR": ("#CBD4E0", "#4E5D73"),
        "SCROLLBAR_HOVER": ("#AFBDCF", "#687B96"), "PRIMARY_FILL": ("#52657D", "#5B6D86"),
        "PRIMARY_HOVER": ("#3F5269", "#607591"), "SUCCESS": ("#327762", "#8BD0B4"),
    },
}

# Halo, paper, outline, headphones, shadow and small accent for each mode.
ARTWORK_COLORS = {
    "lavender": (("#F2ECFB", "#FFFFFF", "#79638F", "#9473D2", "#E1D3F4", "#C8E6DA"),
                 ("#342A47", "#30283F", "#C4AEDF", "#B99CE9", "#4A385F", "#7EBAA7")),
    "ocean": (("#E9F2FA", "#FFFFFF", "#5D7E9B", "#6FA2CD", "#CFDFEE", "#BEDFD8"),
              ("#2B3F56", "#26374A", "#A5BED6", "#8DC5F4", "#3F5873", "#7EBAAF")),
    "forest": (("#EAF4EC", "#FFFFFF", "#6D8A73", "#77A181", "#D2E5D6", "#C2DFD2"),
               ("#2F4537", "#2A3A2F", "#ABC8B1", "#9FD5B2", "#45604D", "#8BC6A9")),
    "sepia": (("#F4EBD9", "#FFFEFA", "#9A8060", "#B49364", "#E8D8BC", "#CCD7B8"),
              ("#463A29", "#3B3124", "#CBB18C", "#DABA8F", "#614E33", "#A8BC91")),
    "graphite": (("#EDF1F6", "#FFFFFF", "#7B8BA2", "#8D9FB8", "#D9E1EC", "#C5DED7"),
                 ("#343D4D", "#2E3440", "#B1C2D9", "#B9C9DF", "#48566D", "#8FBEAF")),
}


def get_palette(name):
    if not isinstance(name, str) or name not in PALETTES:
        raise ValueError("请选择有效的主题配色。")
    return PALETTES[name]


class ThemeColor(tuple):
    """A CTk-compatible color pair carrying its semantic role at construction.

    ReaderUI records these roles before resolving the widget arguments. Live
    changes then configure colors only, without rebuilding text or task state.
    """
    def __new__(cls, role):
        value = super().__new__(cls, PALETTES["lavender"][role])
        value.role = role
        return value
