from PIL import Image
import numpy as np

input_path = r"C:\Users\Windows\Desktop\Output\1\旧海口.png"
output_path = r"C:\Users\Windows\Desktop\Output\1\旧海口.png"

tolerance = 50   # 全局容差

img = Image.open(input_path).convert('RGB')
data = np.array(img)

# 判断近黑
mask = (data[:, :, 0] < tolerance) & (data[:, :, 1] < tolerance) & (data[:, :, 2] < tolerance)

# 近黑统一转为纯黑 (0,0,0)
data[mask] = [0, 0, 0]
# 非近黑设为白色
data[~mask] = [255, 255, 255]

result = Image.fromarray(data)
result.save(output_path)
print(f"处理完成，保存至：{output_path}")