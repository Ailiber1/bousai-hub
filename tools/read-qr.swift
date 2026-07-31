// 生成したQRコードを実際に読み取って、正しいURLが入っているか検証する。
// 誤ったQRを配布すると実害が出るため、生成しただけで終わらせない。
import Foundation
import CoreImage
import AppKit

let path = CommandLine.arguments[1]
guard let img = CIImage(contentsOf: URL(fileURLWithPath: path)) else {
    print("読み込み失敗"); exit(1)
}
let detector = CIDetector(ofType: CIDetectorTypeQRCode, context: nil,
                          options: [CIDetectorAccuracy: CIDetectorAccuracyHigh])!
let features = detector.features(in: img)
if features.isEmpty { print("QRを検出できませんでした"); exit(1) }
for f in features {
    if let q = f as? CIQRCodeFeature {
        print("読み取り結果: \(q.messageString ?? "(空)")")
    }
}
