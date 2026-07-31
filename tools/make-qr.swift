// 防災ハブのQRコードを生成する。
// macOS標準のCore Image(CIQRCodeGenerator)を使うので、外部パッケージのインストールは不要。
import Foundation
import CoreImage
import AppKit

let url = "https://ailiber1.github.io/bousai-hub/"
let outPath = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "qr.png"
let side: CGFloat = 1200   // 印刷しても粗くならない大きさ

guard let filter = CIFilter(name: "CIQRCodeGenerator") else { exit(1) }
filter.setValue(url.data(using: .utf8), forKey: "inputMessage")
// 誤り訂正レベル H = 最大。印刷物が汚れたり折れても読み取れるようにする
filter.setValue("H", forKey: "inputCorrectionLevel")

guard let out = filter.outputImage else { exit(1) }
let scale = side / out.extent.width
let scaled = out.transformed(by: CGAffineTransform(scaleX: scale, y: scale))

let rep = NSCIImageRep(ciImage: scaled)
let img = NSImage(size: rep.size)
img.addRepresentation(rep)

guard let tiff = img.tiffRepresentation,
      let bmp = NSBitmapImageRep(data: tiff),
      let png = bmp.representation(using: .png, properties: [:]) else { exit(1) }

try png.write(to: URL(fileURLWithPath: outPath))
print("生成: \(outPath) (\(Int(rep.size.width))x\(Int(rep.size.height)))")
