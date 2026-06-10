# Apple Photos edit terminology glossary

> Date: 2026-06-08
> Scope: iPhotron edit sidebar terms that intentionally mirror macOS Photos edit controls.
> Rule: when a project edit control maps to Apple Photos, use the Apple Support zh-CN and de-DE terms below.

## Sources

- Adjust light, color, and black-and-white: https://support.apple.com/zh-cn/guide/photos/pht806aea6a6/mac and https://support.apple.com/de-de/guide/photos/pht806aea6a6/mac
- White balance: https://support.apple.com/zh-cn/guide/photos/pht9b1d4a744/mac and https://support.apple.com/de-de/guide/photos/pht9b1d4a744/mac
- Selective color: https://support.apple.com/zh-cn/guide/photos/phtcafe645b6/mac and https://support.apple.com/de-de/guide/photos/phtcafe645b6/mac
- Levels, definition, sharpen, noise reduction, and vignette: Apple Photos support pages under `support.apple.com/*/guide/photos/`.

## Glossary

| English source | zh-CN official | de-DE official | Implementation note |
| --- | --- | --- | --- |
| Light | 光效 | Licht | Edit section title. |
| Color | 颜色 | Farbe | Edit section title. |
| Black & White | 黑白 | Schwarzweiß | Edit section title. |
| Brilliance | 鲜明度 | Brillanz | Light sub-control. |
| Exposure | 曝光 | Belichtung | Light sub-control. |
| Highlights | 高光 | Lichter | Light sub-control. |
| Shadows | 阴影 | Schatten | Light sub-control. |
| Brightness | 亮度 | Helligkeit | Light sub-control. |
| Contrast | 对比度 | Kontrast | Light sub-control. |
| Black Point | 黑点 | Schwarzpunkt | Light sub-control. |
| Saturation | 饱和度 | Sättigung | Color and selective color sub-control. |
| Vibrance | 自然饱和度 | Lebendigkeit | Color sub-control. |
| Cast | 色偏 | Farbstich | Color sub-control. |
| Intensity | 强度 | Intensität | Black-and-white and sharpen control. |
| Neutrals | 中性 | Neutraltöne | Black-and-white control. |
| Tone | 色调 | Ton | Black-and-white control. |
| Grain | 颗粒 | Körnung | Black-and-white control. |
| White Balance | 白平衡 | Weißabgleich | Edit section title. |
| Neutral Gray | 中性灰色 | Neutrales Grau | White balance mode. |
| Skin Tone | 肤色 | Hautton | White balance mode. |
| Temperature/Tint | 色温/色调 | Temperatur/Farbton | White balance mode. |
| Warmth | 暖度 | Wärme | White balance slider. |
| Temperature | 色温 | Temperatur | White balance slider. |
| Tint | 色调 | Farbton | White balance slider. |
| Curve | 曲线 | Kurven | Edit section title. |
| Levels | 色阶 | Tonwerte | Edit section title. |
| Definition | 清晰度 | Auflösung | Edit section title/control. |
| Selective Color | 可选颜色 | Selektive Farbe | Edit section title. |
| Hue | 色调 | Farbton | Selective color slider. |
| Luminance | 亮度 | Leuchtkraft | Selective color slider. |
| Range | 范围 | Bereich | Selective color slider. |
| Noise Reduction | 减少噪点 | Bildrauschen reduzieren | Edit section title. |
| Amount | 强度 | Stärke | Noise reduction slider. |
| Sharpen | 锐化 | Scharfzeichnen | Edit section title. |
| Edges | 边缘 | Kanten | Sharpen slider. |
| Falloff | 衰减 | Abnahme | Sharpen slider. |
| Vignette | 晕影 | Vignette | Edit section title. |
| Strength | 强度 | Stärke | Vignette slider. |
| Radius | 半径 | Radius | Vignette slider. |
| Softness | 柔和度 | Weichheit | Vignette slider. |

## Maintenance Notes

- Keep English source text stable in code and translate via `tr()`/Qt `.ts` resources.
- Do not use translated labels for edit logic. Use session keys, action IDs, or combo `itemData()`.
- If an Apple term conflicts with a previous project translation, Apple Photos terminology wins for edit controls.
- Tooltips that do not have a direct Apple Photos label may use concise project wording, but should avoid inventing new edit terminology.
