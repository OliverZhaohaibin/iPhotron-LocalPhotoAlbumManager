#!/usr/bin/env python3
"""
裁剪坐标变换演示 (Crop Coordinate Transformation Demo)

这个脚本演示了纹理空间和逻辑空间之间的坐标转换。
This script demonstrates coordinate transformation between texture and logical spaces.
"""

import sys
from pathlib import Path

# Add geometry module directly to avoid Qt dependencies
geometry_path = Path(__file__).parent.parent / "src" / "iPhoto" / "gui" / "ui" / "widgets" / "gl_image_viewer"
sys.path.insert(0, str(geometry_path))

import geometry


def print_separator():
    print("=" * 80)


def demo_basic_transformation():
    """演示基本的坐标转换 (Demonstrate basic coordinate transformation)"""
    print_separator()
    print("演示 1: 基本坐标转换 (Demo 1: Basic Coordinate Transformation)")
    print_separator()
    
    # 纹理空间中的裁剪框 (Crop box in texture space)
    texture_crop = (0.3, 0.7, 0.5, 0.6)  # (cx, cy, width, height)
    print(f"\n纹理空间裁剪框 (Texture Space Crop):")
    print(f"  中心 X (Center X): {texture_crop[0]}")
    print(f"  中心 Y (Center Y): {texture_crop[1]}")
    print(f"  宽度 (Width):     {texture_crop[2]}")
    print(f"  高度 (Height):    {texture_crop[3]}")
    
    # 对于每个旋转步骤，展示逻辑空间坐标
    print("\n不同旋转步骤下的逻辑空间坐标 (Logical Space Coordinates at Different Rotations):")
    
    for steps in range(4):
        logical_crop = geometry.texture_crop_to_logical(texture_crop, steps)
        rotation_deg = steps * 90
        
        print(f"\n  旋转 {rotation_deg}° (Rotate {rotation_deg}°) [step={steps}]:")
        print(f"    逻辑中心 X (Logical CX): {logical_crop[0]:.3f}")
        print(f"    逻辑中心 Y (Logical CY): {logical_crop[1]:.3f}")
        print(f"    逻辑宽度 (Logical W):   {logical_crop[2]:.3f}")
        print(f"    逻辑高度 (Logical H):   {logical_crop[3]:.3f}")


def demo_roundtrip_conversion():
    """演示往返转换的无损性 (Demonstrate lossless roundtrip conversion)"""
    print_separator()
    print("演示 2: 往返转换验证 (Demo 2: Roundtrip Conversion Verification)")
    print_separator()
    
    original = (0.25, 0.75, 0.4, 0.6)
    print(f"\n原始纹理坐标 (Original Texture Coordinates): {original}")
    
    for steps in range(4):
        # 纹理 → 逻辑 (Texture → Logical)
        logical = geometry.texture_crop_to_logical(original, steps)
        
        # 逻辑 → 纹理 (Logical → Texture)
        back_to_texture = geometry.logical_crop_to_texture(logical, steps)
        
        # 检查是否相同 (Check if same)
        match = all(abs(a - b) < 1e-6 for a, b in zip(original, back_to_texture))
        status = "✓ 一致" if match else "✗ 不一致"
        
        print(f"\n  旋转 {steps * 90}° (step={steps}):")
        print(f"    原始 (Original):  {original}")
        print(f"    逻辑 (Logical):   {logical}")
        print(f"    还原 (Restored):  {back_to_texture}")
        print(f"    验证 (Verify):    {status}")


def demo_user_interaction_flow():
    """演示用户交互的完整流程 (Demonstrate complete user interaction flow)"""
    print_separator()
    print("演示 3: 用户交互流程 (Demo 3: User Interaction Flow)")
    print_separator()
    
    # 场景: 用户旋转图像 90° 后进入裁剪模式
    # Scenario: User rotates image 90° and enters crop mode
    
    print("\n场景 (Scenario): 图像旋转 90° CW，用户调整裁剪框")
    print("Scenario: Image rotated 90° CW, user adjusts crop box\n")
    
    # 步骤 1: 从 sidecar 加载的存储值 (纹理空间)
    # Step 1: Stored values loaded from sidecar (texture space)
    stored_values = {
        "Crop_CX": 0.5,
        "Crop_CY": 0.5,
        "Crop_W": 0.8,
        "Crop_H": 0.6,
        "Crop_Rotate90": 1.0,
    }
    
    print("步骤 1: 从存储加载纹理坐标 (Step 1: Load texture coords from storage)")
    print(f"  Crop_CX (纹理): {stored_values['Crop_CX']}")
    print(f"  Crop_CY (纹理): {stored_values['Crop_CY']}")
    print(f"  Crop_W (纹理):  {stored_values['Crop_W']}")
    print(f"  Crop_H (纹理):  {stored_values['Crop_H']}")
    print(f"  Rotate90:      {int(stored_values['Crop_Rotate90'])}")
    
    # 步骤 2: 转换到逻辑空间用于交互
    # Step 2: Convert to logical space for interaction
    logical_values = geometry.logical_crop_mapping_from_texture(stored_values)
    
    print("\n步骤 2: 转换到逻辑空间 (Step 2: Convert to logical space)")
    print(f"  Crop_CX (逻辑): {logical_values['Crop_CX']:.3f}")
    print(f"  Crop_CY (逻辑): {logical_values['Crop_CY']:.3f}")
    print(f"  Crop_W (逻辑):  {logical_values['Crop_W']:.3f}")
    print(f"  Crop_H (逻辑):  {logical_values['Crop_H']:.3f}")
    
    # 步骤 3: 用户在逻辑空间中编辑 (模拟向右移动)
    # Step 3: User edits in logical space (simulate moving right)
    print("\n步骤 3: 用户拖动裁剪框 (Step 3: User drags crop box)")
    print("  操作: 向右移动 0.1 单位 (Action: Move right by 0.1 units)")
    
    edited_logical = (
        logical_values['Crop_CX'] + 0.1,  # 向右移动
        logical_values['Crop_CY'],
        logical_values['Crop_W'],
        logical_values['Crop_H'],
    )
    
    print(f"  编辑后逻辑坐标 (Edited logical coords): {edited_logical}")
    
    # 步骤 4: 转换回纹理空间用于存储
    # Step 4: Convert back to texture space for storage
    rotate_steps = int(stored_values['Crop_Rotate90'])
    texture_coords = geometry.logical_crop_to_texture(edited_logical, rotate_steps)
    
    print("\n步骤 4: 转换回纹理空间以保存 (Step 4: Convert back to texture space)")
    print(f"  Crop_CX (纹理): {texture_coords[0]:.3f}")
    print(f"  Crop_CY (纹理): {texture_coords[1]:.3f}")
    print(f"  Crop_W (纹理):  {texture_coords[2]:.3f}")
    print(f"  Crop_H (纹理):  {texture_coords[3]:.3f}")
    
    print("\n✓ 纹理坐标独立于旋转，可以安全保存到 sidecar")
    print("✓ Texture coordinates are rotation-independent, safe to save to sidecar")


def demo_why_two_spaces():
    """解释为什么需要两套坐标系 (Explain why we need two coordinate spaces)"""
    print_separator()
    print("演示 4: 为什么需要两套坐标系？(Demo 4: Why Two Coordinate Spaces?)")
    print_separator()
    
    print("\n如果只用纹理空间 (If we only used texture space):")
    print("  ❌ 用户向右拖动，但旋转 90° 后实际需要修改 Y 坐标")
    print("  ❌ 所有交互逻辑需要根据 rotate_steps 做条件判断")
    print("  ❌ 代码复杂度增加，容易出错")
    
    print("\n如果只用逻辑空间 (If we only used logical space):")
    print("  ❌ 存储的坐标会随旋转变化")
    print("  ❌ 多次旋转会累积浮点误差")
    print("  ❌ 无法直接用于 GPU 纹理采样")
    
    print("\n使用双坐标系的优势 (Advantages of dual coordinate system):")
    print("  ✓ 纹理空间: 存储稳定，不随旋转变化")
    print("  ✓ 逻辑空间: 交互直观，符合视觉方向")
    print("  ✓ 转换函数: 纯函数，易测试，无副作用")
    print("  ✓ 职责分离: 存储层和交互层解耦")


def main():
    """运行所有演示 (Run all demos)"""
    print("\n" + "=" * 80)
    print("裁剪坐标变换演示程序")
    print("Crop Coordinate Transformation Demo")
    print("=" * 80)
    
    demo_basic_transformation()
    print("\n")
    
    demo_roundtrip_conversion()
    print("\n")
    
    demo_user_interaction_flow()
    print("\n")
    
    demo_why_two_spaces()
    print("\n")
    
    print_separator()
    print("演示完成！(Demo Complete!)")
    print("详细文档请参考: docs/CROP_TRANSFORMATION_ANALYSIS.md")
    print("For detailed documentation, see: docs/CROP_TRANSFORMATION_ANALYSIS.md")
    print_separator()


if __name__ == "__main__":
    main()
